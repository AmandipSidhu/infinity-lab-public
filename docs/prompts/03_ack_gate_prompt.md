# Task: Implement Slack Ack Gate (`ack_gate.py` & `slack_api.py`)

Please implement the single acknowledgment gate for our build pipeline.

## Requirements:
1. Create `scripts/slack_api.py` as a lightweight stateless wrapper for Slack Web API (`chat.postMessage`, `conversations.replies`). 
2. Create `scripts/ack_gate.py` which:
   - Accepts a list of WARNs from the validator and reviewer.
   - If zero WARNs, exits immediately (Pass).
   - If WARNs exist, posts a summary message to the Slack channel defined in `SLACK_ACK_CHANNEL_ID`.
   - Generates a unique 6-character `ACK_TOKEN`.
   - Polls the Slack thread (using `conversations.replies`) for a response matching `ACK <ACK_TOKEN>`.
   - Implements a 2-hour timeout.
   - Writes an audit JSON file upon success.
3. Create `tests/test_ack_gate.py` to verify polling, token matching, and timeout logic.

## Constraints:
- NO PLACEHOLDERS. Write the actual polling loop, timeout calculation, and API requests.
- Ensure exceptions during Slack polling bubble up clearly if network drops, but use retry logic for standard HTTP 429s/500s.