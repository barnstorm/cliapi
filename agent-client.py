#!/usr/bin/env python3
"""
Client for agent-daemon. Sends prompt, collects response, clears context.
"""

import json
import sys
import os
import argparse
import select
from typing import Generator

FIFO_IN = "/tmp/agent-in"
FIFO_OUT = "/tmp/agent-out"


def send_message(content: str) -> None:
    """Send a user message to the daemon."""
    msg = {
        "type": "user",
        "message": {
            "role": "user",
            "content": content
        }
    }
    with open(FIFO_IN, "w") as f:
        f.write(json.dumps(msg) + "\n")
        f.flush()


def send_clear() -> None:
    """Send /clear command to reset context."""
    msg = {
        "type": "user",
        "message": {
            "role": "user",
            "content": "/clear"
        }
    }
    with open(FIFO_IN, "w") as f:
        f.write(json.dumps(msg) + "\n")
        f.flush()


def read_responses(timeout: float = 120.0) -> Generator[dict, None, None]:
    """Read stream-json responses until assistant turn completes."""
    with open(FIFO_OUT, "r") as f:
        fd = f.fileno()
        buffer = ""
        
        while True:
            ready, _, _ = select.select([fd], [], [], timeout)
            if not ready:
                raise TimeoutError("Response timeout")
            
            chunk = os.read(fd, 4096).decode("utf-8")
            if not chunk:
                continue
            
            buffer += chunk
            
            while "\n" in buffer:
                line, buffer = buffer.split("\n", 1)
                if not line.strip():
                    continue
                
                try:
                    msg = json.loads(line)
                except json.JSONDecodeError:
                    continue
                
                yield msg

                # Check for turn completion
                msg_type = msg.get("type", "")

                # result signals end of assistant response
                if msg_type == "result":
                    return

                # Also stop on errors
                if msg_type == "error":
                    return


def extract_text(responses: list[dict]) -> str:
    """Extract assistant text from response stream."""
    parts = []

    for msg in responses:
        msg_type = msg.get("type", "")

        # result contains the final text
        if msg_type == "result":
            return msg.get("result", "")

        # assistant message contains content blocks
        elif msg_type == "assistant":
            message = msg.get("message", {})
            content = message.get("content", [])
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text":
                    parts.append(block.get("text", ""))

        # content_block_delta has incremental text
        elif msg_type == "content_block_delta":
            delta = msg.get("delta", {})
            if delta.get("type") == "text_delta":
                parts.append(delta.get("text", ""))

    return "".join(parts)


def wrap_prompt(prompt: str) -> str:
    """Add one-shot execution instructions."""
    return f"""{prompt}

---
IMPORTANT: This is a non-interactive, one-shot execution. You must:
- Complete the entire task in a single response
- Do NOT ask clarifying questions—make reasonable assumptions and state them
- Do NOT wait for confirmation—proceed with the most sensible approach
- If multiple interpretations exist, pick the most likely one and note your choice
- Provide complete, working output rather than partial solutions"""


def call(prompt: str, clear: bool = True, timeout: float = 120.0, raw_prompt: bool = False) -> str:
    """
    Send prompt to daemon, get response, optionally clear context.
    
    Args:
        prompt: The user prompt
        clear: Whether to send /clear after (default True)
        timeout: Response timeout in seconds
        raw_prompt: Skip prompt wrapping
    
    Returns:
        Assistant response text
    """
    if not os.path.exists(FIFO_IN):
        raise RuntimeError(f"Daemon not running (no {FIFO_IN})")
    
    if not raw_prompt:
        prompt = wrap_prompt(prompt)
    
    send_message(prompt)
    responses = list(read_responses(timeout))
    result = extract_text(responses)
    
    if clear:
        send_clear()
        # Drain the clear confirmation
        try:
            list(read_responses(timeout=5.0))
        except TimeoutError:
            pass
    
    return result


def main():
    parser = argparse.ArgumentParser(description="Send prompt to agent daemon")
    parser.add_argument("prompt", nargs="?", help="Prompt text")
    parser.add_argument("-f", "--file", help="Read prompt from file")
    parser.add_argument("--no-clear", action="store_true", help="Don't clear context after")
    parser.add_argument("--raw-prompt", action="store_true", help="Don't wrap prompt with one-shot instructions")
    parser.add_argument("-t", "--timeout", type=float, default=120.0)
    parser.add_argument("--raw", action="store_true", help="Output raw JSON responses")
    
    args = parser.parse_args()
    
    if args.file:
        with open(args.file) as f:
            prompt = f.read()
    elif args.prompt:
        prompt = args.prompt
    elif not sys.stdin.isatty():
        prompt = sys.stdin.read()
    else:
        parser.error("No prompt provided")
    
    if args.raw:
        if not args.raw_prompt:
            prompt = wrap_prompt(prompt)
        send_message(prompt)
        for msg in read_responses(args.timeout):
            print(json.dumps(msg))
        if not args.no_clear:
            send_clear()
            try:
                for msg in read_responses(timeout=5.0):
                    print(json.dumps(msg))
            except TimeoutError:
                pass
    else:
        result = call(prompt, clear=not args.no_clear, timeout=args.timeout, raw_prompt=args.raw_prompt)
        print(result)


if __name__ == "__main__":
    main()
