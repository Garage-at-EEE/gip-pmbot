import os
import logging
from telegram import Update
from telegram.ext import ApplicationBuilder, ContextTypes, CommandHandler, MessageHandler, filters
from google.oauth2 import service_account
from googleapiclient.discovery import build
from google.genai import types
from google import genai
from dotenv import load_dotenv

load_dotenv()

# Enable logging so you can see messages and errors in your VS Code terminal
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

# # --- CONFIGURATION ---
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
FOLDER_ID = os.getenv("FOLDER_ID")
CREDENTIALS_FILE = os.getenv("CREDENTIALS_FILE")

def fetch_folder_recursive(service, folder_id, documents_dict):
    """Recursively crawls subfolders and extracts text from docs, sheets, slides, and text files."""
    try:
        query = f"'{folder_id}' in parents and trashed = false"
        results = service.files().list(q=query, pageSize=100, fields="files(id, name, mimeType)").execute()
        files = results.get('files', [])
        
        for file in files:
            file_id = file['id']
            file_name = file['name']
            mime_type = file['mimeType']
            
            # If it's a subfolder, dive deeper into it!
            if mime_type == 'application/vnd.google-apps.folder':
                logger.info(f"📂 Entering subfolder: {file_name}")
                fetch_folder_recursive(service, file_id, documents_dict)
                continue
            
            try:
                content = ""
                # Google Docs -> Export as plain text
                if mime_type == 'application/vnd.google-apps.document':
                    request = service.files().export_media(fileId=file_id, mimeType='text/plain')
                    content = request.execute().decode('utf-8')
                
                # Google Slides -> Export as plain text
                elif mime_type == 'application/vnd.google-apps.presentation':
                    request = service.files().export_media(fileId=file_id, mimeType='text/plain')
                    content = request.execute().decode('utf-8')
                
                # Google Sheets -> Export as CSV text
                elif mime_type == 'application/vnd.google-apps.spreadsheet':
                    request = service.files().export_media(fileId=file_id, mimeType='text/csv')
                    content = request.execute().decode('utf-8')
                
                # Plain text, markdown, or json
                elif mime_type in ['text/plain', 'application/json', 'text/markdown']:
                    request = service.files().get_media(fileId=file_id)
                    content = request.execute().decode('utf-8')
                else:
                    continue
                
                if content:
                    documents_dict[file_name] = content
                    logger.info(f" Loaded file: {file_name}")
                    
            except Exception as file_err:
                logger.warning(f"⚠️ Could not read file {file_name}: {file_err}")
                
    except Exception as e:
        logger.error(f"Error crawling folder {folder_id}: {e}")

def fetch_drive_documents():
    """Initializes Drive service and starts recursive folder scanning."""
    logger.info("Fetching documents from Google Drive across subfolders...")
    try:
        SCOPES = ['https://www.googleapis.com/auth/drive.readonly']
        creds = service_account.Credentials.from_service_account_file(
            CREDENTIALS_FILE, scopes=SCOPES
        )
        service = build('drive', 'v3', credentials=creds)
        
        documents = {}
        fetch_folder_recursive(service, FOLDER_ID, documents)
        
        if not documents:
            documents["Notice.txt"] = "The folder or subfolders contain no readable text files, docs, slides, or sheets."
            
        return documents
    except Exception as e:
        logger.error(f"Error connecting to Google Drive: {e}")
        return {"Notice.txt": "Could not connect to Google Drive. Check credentials and folder permissions."}

# Pre-load documents when the script starts
drive_docs = fetch_drive_documents()
context_corpus = "\n".join([f"=== FILE: {name} ===\n{content}\n" for name, content in drive_docs.items()])

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Sends a welcome message when a user starts the bot."""
    user_name = update.effective_user.first_name
    welcome_text = (
        f"Hello {user_name}! 👋 I am your NTU Garage AI Assistant.\n\n"
        f"I have successfully indexed {len(drive_docs)} files from all subfolders, docs, slides, and sheets. Ask me anything about our events, schedules, or guidelines!"
    )
    await update.message.reply_text(welcome_text)

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles incoming chat questions, queries Gemini with folder context, and replies."""
    user_message = update.message.text
    logger.info(f"Received question: {user_message}")
    
    # Let the user know the bot is thinking
    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")
    
    try:
        client = genai.Client(api_key=GEMINI_API_KEY)
        
        full_prompt = (
            f"Here are the documents from our team's Google Drive folder and subfolders:\n"
            f"{context_corpus}\n\n"
            f"User Question: {user_message}"
        )
        
        response = client.models.generate_content(
            model='gemini-3.5-flash-lite',
            contents=full_prompt,
            config=types.GenerateContentConfig(
                system_instruction=(
                    "You are the official conversational assistant for NTU Garage EEE. "
                    "Answer user questions accurately, naturally, and helpfully based strictly on the provided Google Drive folder contents."
                ),
                temperature=0.3,
            ),
        )
        
        reply_text = response.text
    except Exception as e:
        logger.error(f"AI Generation Error: {e}")
        reply_text = "Sorry! I encountered an error while processing your question with Gemini."
        
    await update.message.reply_text(reply_text)

if __name__ == "__main__":
    print("🤖 Starting Telegram AI Agent...")
    
    # Build the Telegram application
    app = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).build()
    
    # Register handlers
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), handle_message))
    
    print(" Bot is running and listening for messages! Press CTRL+C to stop.")
    app.run_polling()