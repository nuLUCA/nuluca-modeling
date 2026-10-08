"""Mint or revoke one QA-owned member key through the deployed authenticated page."""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

NAME = "SPRINT-703 W10 member canvas"
SITE = "https://nuluca-frontend-staging.up.railway.app"


def main(command: str, output: Path) -> None:
    state = os.environ["NULUCA_E2E_AUTH_STATE"]
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel="chrome", headless=True)
        context = browser.new_context(storage_state=state)
        page = context.new_page()
        page.goto(SITE + "/profile/agent-connect", wait_until="domcontentloaded")
        page.get_by_role("heading", name="Connected agents").wait_for()
        if command == "mint":
            page.get_by_label("Name").fill(NAME)
            page.get_by_label("Modeling tools").check()
            page.get_by_role("button", name="Create key").click()
            key = page.get_by_test_id("agent-connect-key").text_content() or ""
            if not re.fullmatch(r"ae_(?:test|live)_[A-Za-z0-9_-]+", key):
                raise RuntimeError("Member key was not returned by the page")
            output.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            fd = os.open(output, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            with os.fdopen(fd, "w") as stream:
                stream.write(key + "\n")
            print("QA modeling key minted and saved privately")
        elif command == "revoke":
            row = page.locator("li").filter(has_text=NAME).filter(
                has=page.get_by_role("button", name="Turn off")).first
            row.get_by_role("button", name="Turn off").click()
            page.get_by_test_id("agent-connect-key").wait_for(state="detached")
            print("QA modeling key revoked")
        else:
            raise ValueError("Use mint or revoke")
        context.close()
        browser.close()


if __name__ == "__main__":
    main(sys.argv[1], Path(sys.argv[2]))
