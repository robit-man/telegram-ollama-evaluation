# Telegram + Ollama Chat Bot API Documentation

This document explains the features, configuration, and usage of the Telegram + Ollama chat bot. The bot is built using the [python-telegram-bot](https://github.com/python-telegram-bot/python-telegram-bot) library and the [Ollama Python library](https://pypi.org/project/ollama/). It supports persistent chat history, dynamic configuration, auto-reload on changes, intermediate decision-making before replying, tool calling, and message splitting. The bot also handles both private and group chats by exposing sender information in group contexts.

---

## Table of Contents

- [Features](#features)
- [Installation and Setup](#installation-and-setup)
- [Configuration](#configuration)
- [Persistent Chat History](#persistent-chat-history)
- [Intermediate Decision and Tool Calling](#intermediate-decision-and-tool-calling)
- [Telegram Integration and Message Handling](#telegram-integration-and-message-handling)
- [Usage and Examples](#usage-and-examples)
- [Additional Telegram Methods](#additional-telegram-methods)
- [Troubleshooting](#troubleshooting)

---

## Features

- **Virtual Environment Setup:**  
  Automatically creates and uses a Python virtual environment if one is not already active.

- **Dynamic Configuration:**  
  Loads settings from a `config.json` file. The configuration includes:
  - `model`: Primary model for generating responses.
  - `intermediate_model`: Model used for intermediate decision-making.
  - `system`: System prompt for the conversation.
  - `stream`: Enables streaming responses from Ollama.
  - `history`: Directory where chat histories are stored.

- **Persistent Chat History:**  
  Stores conversation history in dedicated JSON files (named by chat ID) for both private and group chats. In group chats, each entry includes the sender's name and a timestamp.

- **Intermediate Decision Making:**  
  Before replying, the bot performs an intermediate inference step (using the `intermediate_model`) based on the last five messages. This step can be bypassed if:
  - The incoming message is a reply to the bot.
  - The bot is explicitly mentioned in the message.

- **Tool Calling:**  
  The bot scans for tool calls within the model’s output (wrapped in triple backticks with the label `tool_code`). If a tool call is detected, it executes the function (from a predefined set) and integrates the result into the conversation.

- **Message Splitting:**  
  Long responses are split into chunks (by sentence) to comply with Telegram's maximum message length.

- **Group Chat Enhancements:**  
  In group chats, the bot appends the sender’s name (in parentheses) to the user’s message before sending it to the model. This allows the model to know who said what.

- **Auto-Reload:**  
  The bot monitors its configuration and source file for changes. If a change is detected, it reloads itself automatically.

---

## Installation and Setup

### Prerequisites

- **Python 3.8+**  
- **Telegram Bot Token:** Create a bot using [BotFather](https://core.telegram.org/bots#botfather) and obtain your bot token.
- **Ollama Server:** Make sure Ollama is installed, running, and the necessary models are pulled (e.g., `gemma3:4b`).

### Virtual Environment Setup

The script automatically checks if it’s running in a virtual environment. If not, it will:
1. Create a virtual environment in the `venv` directory.
2. Install required dependencies:
   - `ollama`
   - `python-telegram-bot==20.7`
   - `python-dotenv`
   - `nest_asyncio`
3. Relaunch itself within the virtual environment.

_No additional manual steps are needed for setting up the virtual environment._

---

## Configuration

The bot’s behavior is controlled by the `config.json` file. Below are some key configuration parameters:

```json
{
  "model": "gemma3:4b",
  "intermediate_model": "gemma3:4b",
  "system": "You are a helpful assistant. Answer questions clearly and concisely.",
  "stream": true,
  "history": "history"
}
```

- **model:**  
  The primary model used for generating replies.

- **intermediate_model:**  
  The model used in the intermediate inference step (i.e., to decide whether to reply or only observe).

- **system:**  
  The system prompt that sets the context for the conversation.

- **stream:**  
  A boolean flag to determine if responses should be streamed.

- **history:**  
  The folder name (or path) where chat history files are stored. Each chat (private or group) will have its own JSON file named with the chat ID.

You can override these settings via command-line arguments when launching the bot.

---

## Persistent Chat History

- **File Structure:**  
  For each conversation (private chat or group), the bot creates a dedicated history file named `<chat_id>.json` in the `history` folder.
  
- **Stored Fields:**  
  Each message entry is stored with:
  - `role`: "user" or "assistant"
  - `content`: The text of the message
  - `timestamp`: The time the message was received (formatted as `YYYY-MM-DD HH:MM:SS`)
  - `sender` (optional): The sender’s username (only for group chats)

---

## Intermediate Decision and Tool Calling

### Intermediate Decision

Before sending a reply, the bot runs an intermediate decision step using the last five messages of the conversation. This step asks:
> "Based on the following conversation, should you reply to the user or simply observe and add to history? Answer exactly with 'reply' or 'observe'. If unsure, answer 'reply'."

The bot then:
- Builds a payload containing the system prompt and the combined text of the last five messages (with sender info if available).
- Streams the response from the `intermediate_model`.
- If the decision is `"observe"`, the bot does not send a reply.

### Tool Calling

If the response generated by the model contains a tool call (identified by content wrapped in triple backticks with `tool_code`), the bot:
1. Extracts the tool call using `Tools.parse_tool_call()`.
2. Executes the function via `Tools.run_tool()`.
3. Appends the tool’s output (wrapped in triple backticks labeled `tool_output`) to the original user message.
4. Re-invokes the inference with the updated history.

---

## Telegram Integration and Message Handling

### Handling Incoming Messages

1. **Message Reception:**  
   The bot listens for all text messages using the `MessageHandler` with `filters.TEXT`.

2. **Group vs. Private Chat:**  
   - In private chats, the chat history file is named with the chat ID.
   - In group chats, the chat ID is used as the key. Additionally, sender information is appended to the message content before sending it to the model (e.g., `"Hello there (sent by username)"`).

3. **Bypassing Intermediate Decision:**  
   If the incoming message:
   - Is a reply to a bot’s message, or  
   - Contains a mention of the bot (using the `BOT_USERNAME`),  
   then the bot bypasses the intermediate decision step and replies immediately.

4. **Typing Indicator:**  
   While processing the message, the bot continuously sends a typing action to let users know it is working on the response.

5. **Response Splitting:**  
   If the generated reply exceeds Telegram’s maximum message length, the reply is split into multiple messages by sentence.

### Example Handler Workflow

- **User sends a message.**
- The bot logs the message into persistent storage with the sender’s name.
- The bot starts the typing indicator.
- If the message is not a reply or mention, the bot performs the intermediate decision step.
- Based on the decision, it either replies or observes (i.e., only updates history without a reply).
- If the reply contains a tool call, the tool is executed and its result is appended to the conversation.
- The final response is then streamed from Ollama and sent to the chat.

---

## Usage and Examples

### Running the Bot

Run the script directly:
```bash
python your_bot_script.py
```

### Command-Line Arguments

You can override configuration parameters with command-line arguments:
- `--model`: Set the primary model.
- `--stream`: Enable streaming responses.
- `--system`: Override the system prompt.
- `--history`: Specify a different history file or folder.
- `--tools`: Provide a path to a JSON file defining additional tools.
- `--option`: Pass additional model parameters (e.g., `--option temperature=0.7`).

### Example Message Flow in Group Chat

1. **User Message:**  
   "Hello, how are you?"
   
   - The bot logs this as:  
     ```json
     {"role": "user", "content": "Hello, how are you?", "timestamp": "2025-03-27 12:34:56", "sender": "john_doe"}
     ```

2. **Intermediate Decision (if not overridden):**  
   Uses the last 5 messages and asks the intermediate model whether to reply.

3. **Final Response:**  
   The model’s reply is processed, and if a tool call is detected, it executes the function.  
   The final response is sent as one or more messages if it needs splitting.

---

## Additional Telegram Methods

The bot can be extended to use various Telegram Bot API methods. For instance:
- **getMe:** To verify the bot’s identity.
- **sendMessage:** To send text messages.
- **sendChatAction:** To broadcast typing notifications.
- **editMessageText:** To edit messages (if needed).
- **getChat:** To fetch detailed chat information for groups/channels.

These methods are available through the [python-telegram-bot](https://github.com/python-telegram-bot/python-telegram-bot) library. You can modify the bot to call these methods as needed (for example, when setting up a group chat history or retrieving user profiles).

---

## Troubleshooting

- **Bot Not Responding in Groups:**  
  Ensure that the bot has been added to the group and that its privacy mode is disabled (via BotFather) so it can read all messages.

- **Empty Chat History Files:**  
  Verify the `history` directory exists and the bot has write permissions.

- **Intermediate Decision Not Triggering:**  
  Check if messages in group chats are being marked as replies or mentions; in those cases, the intermediate decision is bypassed.

- **Dependency Issues:**  
  Ensure that the virtual environment is activated and all dependencies are installed. The script automatically creates and activates the virtual environment if needed.

