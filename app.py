#!/usr/bin/env python
import os
import sys
import subprocess
import asyncio
import re
import json
import time
from pathlib import Path
import threading
import inspect

#############################################
# Step 1. Virtual Environment Setup
#############################################
VENV_DIR = Path("venv")
IS_WINDOWS = os.name == "nt"

def in_virtualenv():
    return sys.prefix != sys.base_prefix

if not in_virtualenv():
    if not VENV_DIR.exists():
        print("🔧 Creating virtual environment...")
        subprocess.check_call([sys.executable, "-m", "venv", str(VENV_DIR)])
    venv_python = VENV_DIR / ("Scripts" if IS_WINDOWS else "bin") / ("python.exe" if IS_WINDOWS else "python")
    print("📦 Installing dependencies in the virtual environment...")
    subprocess.check_call([str(venv_python), "-m", "pip", "install", "--upgrade", "pip"])
    deps = ["ollama", "python-telegram-bot==20.7", "python-dotenv", "nest_asyncio"]
    subprocess.check_call([str(venv_python), "-m", "pip", "install", *deps])
    print("🚀 Relaunching script inside the virtual environment...")
    os.environ["ENV_ACTIVATED"] = "1"
    subprocess.check_call([str(venv_python), sys.argv[0]] + sys.argv[1:])
    sys.exit(0)

#############################################
# Step 2. Imports (inside venv)
#############################################
from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    ContextTypes,
    MessageHandler,
    CommandHandler,
    filters,
)
from telegram.constants import ChatAction
from telegram.request import HTTPXRequest
from ollama import chat
from dotenv import load_dotenv

#############################################
# Step 3. Load Environment Variables
#############################################
load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise ValueError("Missing BOT_TOKEN in .env file")
# BOT_USERNAME is used to check for mentions.
BOT_USERNAME = os.getenv("BOT_USERNAME", "").lower()

#############################################
# Step 4. Configuration Manager
#############################################
class ConfigManager:
    DEFAULT_CONFIG = {
        "model": "hf.co/soob3123/amoral-gemma3-4B-v1-gguf:latest",
        "intermediate_model": "hf.co/soob3123/amoral-gemma3-4B-v1-gguf:latest",  # Model used for intermediate decision.
        "system": "You are a helpful assistant. Answer questions clearly and concisely.",
        "stream": True,
        "history": "history"  # Folder for history files.
    }
    
    def __init__(self, config_path="config.json"):
        self.config_path = config_path
        if not os.path.exists(config_path):
            with open(config_path, "w") as f:
                json.dump(ConfigManager.DEFAULT_CONFIG, f, indent=2)
            self.config = dict(ConfigManager.DEFAULT_CONFIG)
        else:
            try:
                with open(config_path, "r") as f:
                    self.config = json.load(f)
            except Exception:
                self.config = dict(ConfigManager.DEFAULT_CONFIG)
    
    def update_config(self):
        try:
            with open(self.config_path, "w") as f:
                json.dump(self.config, f, indent=2)
        except Exception as e:
            print("Error updating config:", e)

config_manager = ConfigManager()

#############################################
# Step 5. Persistent Chat History (Dedicated Files)
#############################################
history_config = config_manager.config.get("history", "history")
history_path = Path(history_config)
if not history_path.is_dir():
    history_path = Path("history")
history_path.mkdir(parents=True, exist_ok=True)
HISTORY_DIR = history_path
MAX_HISTORY = 100  # Maximum messages per conversation

def get_history_filepath(key: str) -> Path:
    return HISTORY_DIR / f"{key}.json"

# For both private and group chats, we use the chat id as key.
def get_history_key(update: Update) -> str:
    chat_type = update.message.chat.type
    chat_id = update.effective_chat.id
    if chat_type != "private":
        title = update.message.chat.title or "No Title"
        print(f"📢 Group Message in '{title}' (chat_id: {chat_id})")
    return str(chat_id)

def load_history_for_key(key: str) -> list:
    path = get_history_filepath(key)
    try:
        if path.exists():
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception as e:
        print(f"Error loading history for {key}: {e}")
    return []

def save_history_for_key(key: str, history: list) -> None:
    path = get_history_filepath(key)
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(history, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"Error saving history for {key}: {e}")

# When updating history, we now store sender information and timestamp.
def update_history(key: str, role: str, content: str, sender: str = None) -> list:
    history = load_history_for_key(key)
    entry = {
        "role": role,
        "content": content,
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S")
    }
    if sender:
        entry["sender"] = sender
    history.append(entry)
    if len(history) > MAX_HISTORY:
        history = history[-MAX_HISTORY:]
    save_history_for_key(key, history)
    return history

def reset_history(key: str) -> None:
    save_history_for_key(key, [])

#############################################
# Step 6. Helper to Split Long Messages
#############################################
def split_message(text: str, max_length: int = 4096) -> list:
    if len(text) <= max_length:
        return [text]
    sentences = re.split(r'(?<=[.!?])\s+', text)
    chunks = []
    current_chunk = ""
    for sentence in sentences:
        if len(sentence) > max_length:
            if current_chunk:
                chunks.append(current_chunk.strip())
                current_chunk = ""
            for i in range(0, len(sentence), max_length):
                chunks.append(sentence[i:i+max_length].strip())
            continue
        if len(current_chunk) + len(sentence) + 1 > max_length:
            chunks.append(current_chunk.strip())
            current_chunk = sentence + " "
        else:
            current_chunk += sentence + " "
    if current_chunk:
        chunks.append(current_chunk.strip())
    return chunks

#############################################
# Step 7. Tools Class for Tool Calling
#############################################
class Tools:
    @staticmethod
    def parse_tool_call(text: str) -> str:
        pattern = r"```tool_code\s*(.*?)\s*```"
        match = re.search(pattern, text, re.DOTALL)
        if match:
            return match.group(1).strip()
        return None

    @staticmethod
    def run_tool(tool_code: str) -> str:
        allowed_tools = {
            "echo": lambda x: x,
            # Add additional allowed functions here.
        }
        try:
            result = eval(tool_code, {"__builtins__": {}}, allowed_tools)
            return str(result)
        except Exception as e:
            return f"Error executing tool: {e}"

#############################################
# Step 8. Intermediate Inference Step (Using Last 5 Messages)
#############################################
def intermediate_decision(conversation: list) -> str:
    # Use the last five messages for context.
    recent_conversation = conversation[-5:]
    # For each message, if a sender exists, append it.
    combined = "\n".join([
        f"{msg['role']}: {msg['content']}" + (f" (sent by {msg['sender']})" if "sender" in msg else "")
        for msg in recent_conversation
    ])
    prompt = (
        "Based on the following conversation, should you reply to the user or simply observe and add to history? "
        "Answer exactly with 'reply' or 'observe'. If unsure, answer 'reply'."
    )
    messages = [
        {"role": "system", "content": prompt},
        {"role": "user", "content": combined}
    ]
    
    print("Intermediate decision payload messages:")
    for msg in messages:
        print(f"{msg['role']}: {msg['content']}")
    
    decision = ""
    try:
        stream = chat(
            model=config_manager.config.get("intermediate_model", "gemma3:4b"),
            messages=messages,
            stream=True
        )
        print("Intermediate decision stream output:")
        for chunk in stream:
            if "message" in chunk and "content" in chunk["message"]:
                content = chunk["message"]["content"]
                print("Chunk:", content)
                decision += content
        print("Final assembled decision:", decision)
        decision = decision.strip().lower()
        if not decision:
            print("Intermediate decision empty; defaulting to 'reply'")
            return "reply"
        return "observe" if decision == "observe" else "reply"
    except Exception as e:
        print("Intermediate decision error:", e)
        return "reply"

#############################################
# Step 9. Stream and Consolidate Ollama Response
#############################################
def stream_ollama_chat_response(conversation: list) -> str:
    messages = []
    system_msg = config_manager.config.get("system", "")
    if system_msg:
        messages.append({"role": "system", "content": system_msg})
    # When building payload, include sender info (if exists) for each user message.
    for msg in conversation:
        new_msg = dict(msg)
        if new_msg.get("role") == "user" and "sender" in new_msg:
            new_msg["content"] = f"{new_msg['content']} (sent by {new_msg['sender']})"
        messages.append(new_msg)
    result = ""
    try:
        stream = chat(
            model=config_manager.config.get("model", "gemma3:4b"),
            messages=messages,
            stream=config_manager.config.get("stream", True)
        )
        for chunk in stream:
            if "message" in chunk and "content" in chunk["message"]:
                result += chunk["message"]["content"]
    except Exception as e:
        result = f"Error in streaming response: {e}"
    return result

#############################################
# Step 10. Telegram Handlers
#############################################
def get_sender_name(update: Update) -> str:
    if update.message.from_user:
        user = update.message.from_user
        if user.username:
            return user.username
        else:
            return f"{user.first_name} {user.last_name}" if user.last_name else user.first_name
    return "Unknown"

async def reset_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    key = get_history_key(update)
    reset_history(key)
    await update.message.reply_text("🔄 Your conversation history has been reset.")

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message is None or update.message.text is None:
        return
    user_text = update.message.text.strip()
    key = get_history_key(update)
    sender = get_sender_name(update)
    print(f"📩 Received message from key {key} (sender: {sender}): {user_text}")
    
    # Update persistent history with the user's message including sender info.
    history = update_history(key, "user", user_text, sender=sender)
    
    # Determine if we bypass intermediate decision: if the message is a reply to a bot message or bot is mentioned.
    bypass_intermediate = False
    if update.message.reply_to_message is not None:
        if update.message.reply_to_message.from_user and update.message.reply_to_message.from_user.is_bot:
            bypass_intermediate = True
    if BOT_USERNAME and ("@" + BOT_USERNAME in user_text.lower()):
        bypass_intermediate = True

    # Start typing indicator.
    stop_typing = asyncio.Event()
    async def send_typing():
        while not stop_typing.is_set():
            try:
                await context.bot.send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.TYPING)
            except Exception as e:
                print("Error sending typing action:", e)
            await asyncio.sleep(3)
    typing_task = asyncio.create_task(send_typing())
    
    # Get initial assistant reply.
    try:
        assistant_reply = await asyncio.to_thread(stream_ollama_chat_response, history)
    except Exception as e:
        assistant_reply = f"⚠️ Error processing your message: {e}"
        print(assistant_reply)
    stop_typing.set()
    try:
        await typing_task
    except asyncio.CancelledError:
        pass
    update_history(key, "assistant", assistant_reply, sender="assistant")
    
    # If not bypassing, run the intermediate decision.
    if not bypass_intermediate:
        decision = intermediate_decision(load_history_for_key(key))
        print(f"Intermediate decision: {decision}")
        if decision == "observe":
            print("Decision is to observe; not sending a reply.")
            return
    
    # Check for a tool call in the initial reply.
    tool_code = Tools.parse_tool_call(assistant_reply)
    if tool_code:
        tool_output = Tools.run_tool(tool_code)
        formatted_output = f"```tool_output\n{tool_output}\n```"
        combined_prompt = f"{user_text}\n{formatted_output}"
        update_history(key, "user", combined_prompt, sender=sender)
        final_reply = await asyncio.to_thread(stream_ollama_chat_response, load_history_for_key(key))
        assistant_reply = final_reply
        update_history(key, "assistant", final_reply, sender="assistant")
    
    # Send the final reply (splitting if too long).
    parts = split_message(assistant_reply)
    for part in parts:
        try:
            await update.message.reply_text(part)
        except Exception as e:
            print("Error sending message part:", e)

#############################################
# Step 11. Error Handler
#############################################
async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    print(f"Error while handling update: {context.error}")

#############################################
# Step 12. File Monitoring for Auto-Reload
#############################################
def monitor_file_changes(file_path: str, interval: int = 2):
    last_mtime = os.path.getmtime(file_path)
    while True:
        time.sleep(interval)
        try:
            new_mtime = os.path.getmtime(file_path)
            if new_mtime != last_mtime:
                print(f"Change detected in {file_path}. Reloading...")
                os.execv(sys.executable, [sys.executable] + sys.argv)
        except Exception as e:
            print(f"Error monitoring {file_path}: {e}")

def start_file_monitors():
    config_path = os.path.abspath(config_manager.config_path)
    script_path = os.path.abspath(__file__)
    threading.Thread(target=monitor_file_changes, args=(config_path,), daemon=True).start()
    threading.Thread(target=monitor_file_changes, args=(script_path,), daemon=True).start()

#############################################
# Step 13. Main Bot Runner
#############################################
async def main():
    print("🤖 Starting Telegram bot with global persistent group history, dynamic config, auto-reload, intermediate decision, tool calling, and message splitting...")
    start_file_monitors()
    req = HTTPXRequest(connect_timeout=20, read_timeout=20)
    while True:
        try:
            app = ApplicationBuilder().token(BOT_TOKEN).request(req).build()
            app.add_handler(CommandHandler("reset", reset_command))
            app.add_handler(MessageHandler(filters.TEXT, handle_message))
            app.add_error_handler(error_handler)
            await app.run_polling()
        except Exception as e:
            print("Error while getting updates:", e)
            await asyncio.sleep(5)

#############################################
# Step 14. Launch the Bot (Never Exit)
#############################################
if __name__ == "__main__":
    try:
        import nest_asyncio
        nest_asyncio.apply()
    except ImportError:
        pass
    while True:
        try:
            asyncio.run(main())
        except Exception as e:
            print("Error in main loop:", e)
            time.sleep(5)
