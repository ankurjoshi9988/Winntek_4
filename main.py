import os
import uuid
import asyncio
from flask import Flask, request, render_template, jsonify, send_from_directory, redirect, url_for, session
from flask_session import Session
from flask_login import login_required, current_user
from datetime import timedelta
from conversation_service import start_conversation, add_message, close_conversation, get_past_conversations
import re
from flask_wtf.csrf import CSRFProtect, generate_csrf
import random
from langchain_community.vectorstores import FAISS
from langchain_google_genai import GoogleGenerativeAIEmbeddings
from gtts import gTTS
import google.generativeai as genai
from dotenv import load_dotenv
import json
import glob
import csv
from knowledge import knowledge_bp
from reflect import reflect_bp
from analytics import analytics_bp
from sqlalchemy import func



import logging
import site
print(site.getsitepackages())
import azure.cognitiveservices.speech as speechsdk
from fuzzywuzzy import fuzz
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_core.prompts import ChatPromptTemplate, SystemMessagePromptTemplate, HumanMessagePromptTemplate, PromptTemplate
from langchain_core.messages import HumanMessage, SystemMessage
import aiofiles
from concurrent.futures import ThreadPoolExecutor
from auth import auth_bp, init_auth
from admin import admin_bp
from authlib.integrations.flask_client import OAuth
from models import User, Conversation, Message, Feedback, Persona, ReferConversation, Product
from extensions import login_manager, csrf, mail, oauth, db



load_dotenv()
#os.getenv("GOOGLE_API_KEY")
#genai.configure(api_key=os.getenv("GOOGLE_API_KEY"))


api_key=os.environ['GOOGLE_API_KEY']
genai.configure(api_key=api_key)
azure_subscription_key = os.getenv("AZURE_SUBSCRIPTION_KEY")
azure_region = os.getenv("AZURE_REGION")
llm = ChatGoogleGenerativeAI(model="gemini-pro", convert_system_message_to_human=True)
#llm = ChatGoogleGenerativeAI(model="gemini-1.5-flash-latest", convert_system_message_to_human=True)
#env = os.getenv('FLASK_ENV', 'development')
executor = ThreadPoolExecutor()
# Define voice mappings for male and female personas
VOICE_MAPPING = {
    "Male": "hi-IN-MadhurNeural",
    "Female": "hi-IN-SwaraNeural"
}

app = Flask(__name__)
#env = os.getenv('FLASK_ENV', 'development')
# Configure app with local database
app.config['WTF_CSRF_ENABLED'] = True  # Enable CSRF protection
app.config['SECRET_KEY'] = os.getenv('SECRET_KEY')
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///db.sqlite'

# Configure server-side session
app.config['SESSION_TYPE'] = 'filesystem'  # Use the filesystem to store sessions
app.config['SESSION_FILE_DIR'] = './flask_session/'  # Directory to store session files

# Additional configuration common to all environments
app.config.update({
    'MAIL_SERVER': os.getenv('MAIL_SERVER', 'sandbox.smtp.mailtrap.io'),
    'MAIL_PORT': int(os.getenv('MAIL_PORT', 2525)),
    'MAIL_USERNAME': os.getenv('MAIL_USERNAME'),
    'MAIL_PASSWORD': os.getenv('MAIL_PASSWORD'),
    'MAIL_DEFAULT_SENDER': os.getenv('MAIL_DEFAULT_SENDER'),
    'MAIL_USE_TLS': True,
    'MAIL_USE_SSL': False,
    'PERMANENT_SESSION_LIFETIME': timedelta(minutes=30)
})

# Configuration for Google OAuth
oauth.register(
    name='google',
    client_id=os.getenv('GOOGLE_CLIENT_ID'),
    client_secret=os.getenv('GOOGLE_CLIENT_SECRET'),
    authorize_url='https://accounts.google.com/o/oauth2/auth',
    authorize_params=None,
    access_token_url='https://oauth2.googleapis.com/token',
    access_token_params=None,
    refresh_token_url=None,
    redirect_uri=os.getenv('GOOGLE_REDIRECT_URI'),
    client_kwargs={'scope': 'https://www.googleapis.com/auth/userinfo.email https://www.googleapis.com/auth/userinfo.profile'}
)

# Initialize extensions
db.init_app(app)
login_manager.init_app(app)
login_manager.login_view = 'auth.login'
csrf.init_app(app)
mail.init_app(app)
oauth.init_app(app)
Session(app)  # Initialize the session
# Initialize auth module
init_auth(oauth)



# Register Blueprints
app.register_blueprint(knowledge_bp)
app.register_blueprint(reflect_bp, url_prefix='/reflect')
app.register_blueprint(auth_bp)
app.register_blueprint(admin_bp, url_prefix='/admin')
app.register_blueprint(analytics_bp)


logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler("app_debug.log"),
        logging.StreamHandler()
    ]
)

# Configure other specific loggers
logging.getLogger('hpack').setLevel(logging.WARNING)  # Example: Set hpack logs to WARNING
logging.getLogger('httpx').setLevel(logging.WARNING)  # Example: Set httpx logs to WARNING


async def load_feedback_data(filename):
    feedback_data = []
    async with aiofiles.open(filename, mode="r", encoding="utf-8") as f:
        async for line in f:
            line = line.strip()
            if line:
                try:
                    feedback = json.loads(line)
                    feedback_data.append(feedback)
                except json.JSONDecodeError:
                    print(f"Ignoring invalid JSON data: {line}")
    return feedback_data

positive_feedback = asyncio.run(load_feedback_data("data/positive.json"))
negative_feedback = asyncio.run(load_feedback_data("data/negative.json"))
conversation = asyncio.run(load_feedback_data("data/conversation.json"))

positive_chat = [
    {"agent_message": chat["agent_message"], "customer_message": chat["customer_message"]}
    for chat in positive_feedback
]
negative_chat = [
    {"agent_message": chat["agent_message"], "customer_message": chat["customer_message"]}
    for chat in negative_feedback
]
objection_handling = [
    {"customer_message": chat["customer_message"]}
    for chat in conversation
]

persona_data1 = {}
persona_data2 = {}

def read_persona_details_from_csv(csv_file):
    with open(csv_file, 'r', encoding='utf-8-sig') as file:
        reader = csv.DictReader(file)
        print("CSV Headers:", reader.fieldnames)  # Add this line to debug
        for row in reader:
            # Convert the persona name to lowercase for consistent lookups
            persona_name = row['Name'].strip().lower()
            persona_data1[persona_name] = {
                'Age': row['Age'],
                'Gender': row['Gender'],
                'Occupation': row['Occupation'],
                'Marital Status': row['Marital Status'],
                'Income Range': row['Income Range'],
                'Financial Goals': row['Financial Goals'],
                'Family Member':row['Family Member'],
                'Category': row['Categories']
            }
    return persona_data1


persona_data1 = read_persona_details_from_csv('static/persona_details.csv')

@app.route('/load-refer-personas', methods=['GET'])
@login_required
def load_refer_personas():
    personas = Persona.query.all()  # Fetch all personas from the database

    # Ensure that each persona's data is formatted correctly
    persona_list = [{
        "name": persona.name,
        "occupation": persona.occupation,
        "marital_status": persona.marital_status,
        "age": persona.age,
        "income_range": persona.income_range,
        "dependent_family_members": persona.dependent_family_members,  # Replace location with family members
        "financial_goals": persona.financial_goals,
        "category": persona.category
    } for persona in personas]

    return jsonify({"personas": persona_list})



@app.route('/set_custom_persona', methods=['POST'])
@login_required
def set_custom_persona():
    custom_persona = request.json
    name = custom_persona['name'].strip().lower()  # Corrected method name

    # Check if a persona with the same name already exists for the user
    existing_persona = Persona.query.filter_by(name=name, user_id=current_user.id).first()
    if (existing_persona):
        return jsonify({"status": "Persona with this name already exists"}), 409

    # Create and save the custom persona in the database
    new_persona = Persona(
        name=name,
        age=custom_persona['age'],
        gender=custom_persona['gender'],
        occupation=custom_persona['occupation'],
        marital_status=custom_persona['maritalStatus'],
        income_range='Unknown',
        dependent_family_members=custom_persona['familyMembers'],  # Ensure this matches the model
        financial_goals=custom_persona['financialGoal'],
        category='Custom',
        user_id=current_user.id
    )
    db.session.add(new_persona)
    db.session.commit()

    return jsonify({"status": "Custom persona set successfully"})



print("persona_data2",persona_data2)
"""

@app.route('/get-new-csrf-token', methods=['GET'])
def get_new_csrf_token():
    # Generate a new CSRF token and return it as JSON
    csrf_token = generate_csrf()
    return jsonify({'csrf_token': csrf_token})
    
"""

@app.route('/load-personas')
@login_required
def load_personas():
    try:
        # Load predefined personas
        predefined_personas = Persona.query.filter_by(user_id=None).all()
        # Load custom personas created by the current user
        custom_personas = Persona.query.filter_by(user_id=current_user.id).all()

        personas = predefined_personas + custom_personas

        persona_list = [{
            "id": persona.id,
            "name": persona.name,
            "age": persona.age,
            "gender": persona.gender,
            "occupation": persona.occupation,
            "marital_status": persona.marital_status,
            "income_range": persona.income_range,
            "dependent_family_members": persona.dependent_family_members,
            "financial_goals": persona.financial_goals,
            "category": persona.category
        } for persona in personas]

        return jsonify({"personas": persona_list})

    except Exception as e:
        app.logger.error(f"Error loading personas: {e}")
        return jsonify({"error": "An error occurred while loading the personas"}), 500


@app.route('/get_past_conversations', methods=['GET'])
@login_required
def get_past_conversations_route():
    user_id = current_user.id  # Get the user ID from the current_user object
    past_conversations = get_past_conversations(user_id)
    return jsonify(past_conversations)



@app.route('/save_feedback', methods=['POST'])
@login_required
async def save_feedback():
    feedback_data = await request.get_json()
    customer_message = feedback_data.get("customer_message")
    agent_message = feedback_data.get("agent_message")
    thumbs_feedback = feedback_data.get("feedback")

    if thumbs_feedback not in ["positive", "negative"]:
        return jsonify({"error": "Invalid feedback type"})

    if await feedback_exists("data/" + thumbs_feedback + ".json", agent_message, customer_message, thumbs_feedback):
        return jsonify({"message": "Feedback already exists"})

    await save_to_json("data/" + thumbs_feedback + ".json", agent_message, customer_message, thumbs_feedback)

    return jsonify({"message": "Feedback saved successfully"})


async def feedback_exists(filename, agent_message, customer_message, feedback):
    async with aiofiles.open(filename, mode="r", encoding="utf-8") as f:
        async for line in f:
            data = json.loads(line)
            if (data["agent_message"] == agent_message and
                data["customer_message"] == customer_message and
                data["feedback"] == feedback):
                return True
    return False


async def save_to_json(filename, agent_message, customer_message, feedback):
    data = {
        "agent_message": agent_message,
        "customer_message": customer_message,
        "feedback": feedback,
    }

    async with aiofiles.open(filename, mode="a", encoding="utf-8") as f:
        await f.write(json.dumps(data, ensure_ascii=False) + "\n")


@app.route('/get_persona_details/<persona>')
@login_required
def get_persona_details(persona):
    try:
        persona = persona.lower()
        persona = Persona.query.filter_by(name=persona).first()

        if persona and (persona.user_id is None or persona.user_id == current_user.id):
            return jsonify({
                'name': persona.name,
                'age': persona.age,
                'gender': persona.gender,
                'occupation': persona.occupation,
                'marital_status': persona.marital_status,
                'income_range': persona.income_range,
                'dependent_family_members': persona.dependent_family_members,  # Update here
                'financial_goals': persona.financial_goals,
                'category': persona.category
            })
        else:
            return jsonify({"error": "Persona not found"}), 404
    except Exception as e:
        print("Error:", str(e))
        return jsonify({"error": "Internal server error"}), 500



@app.route('/')
@login_required
def index():
    return render_template('index.html')

@app.route('/analytics')
@login_required
def analytics():
    return render_template('analytics.html')



@app.route("/get_chat")
@login_required
def get_chat():
    chat_file = request.args.get('chatfile', type=str)
    if not re.match(r'^[a-zA-Z0-9_]+\.json$', chat_file):
        return jsonify({"error": "Invalid file name."}), 400

    file_path = os.path.join(app.root_path, 'static', 'chat', chat_file)
    print("Resolved file path:", file_path)  # Make sure this prints the path to the .json file

    if not os.path.isfile(file_path):
        return jsonify({"error": "File not found."}), 404

    return send_from_directory(os.path.join(app.root_path, 'static', 'chat'), chat_file)



@app.route('/reflect.html')
@login_required
def refer():
    return render_template('reflect.html')


@app.route('/rehearse.html')
@login_required
def persona_selection():
    session.pop('conversation_id', None)
    return render_template('rehearse.html')


@app.route('/Chat_hindi.html', methods=['GET'])
@app.route('/Chat_english.html', methods=['GET'])
@login_required
def chat():
    persona = request.args.get('persona')
    language = request.args.get('language')

    if language:
        session['language'] = language
    else:
        language = session.get('language', 'Hindi')  # Default to Hindi if not set
    print(f"Language selected: {language}")

    if language == 'Hindi':
        return render_template('Chat_hindi.html', persona=persona)
    else:
        return render_template('Chat_english.html', persona=persona)



#demo
@app.route('/SampleChat.html')
@login_required
def chat1():
    persona = request.args.get('persona')
    return render_template('SampleChat.html', persona=persona)

# Setup logging
logging.basicConfig(level=logging.INFO)


@app.route('/start_conversation/<persona_name>', methods=['POST'])
@login_required
async def start_conversation1(persona_name):
    print(f"Received request for persona: {persona_name}")

    persona_name = persona_name.strip().lower()  # Convert to lowercase for consistency
    # Initialize or retrieve the conversation

    print(f"lower case persona: {persona_name}")
    conversation_id = session.get('conversation_id')
    print(f"Initial conversation_id: {conversation_id}")

    if not conversation_id:
        conversation_id = start_conversation(current_user.id, persona_name)
        session['conversation_id'] = conversation_id
        session.modified = True  # Mark the session as modified to ensure it's saved
    print(f"New conversation_id: {conversation_id}")

    agent_message = request.json.get('message')
    tone = request.json.get('tone', session.get('tone', 'polite'))  # Default tone to 'polite' if not provided
    language = request.json.get('language', session.get('language', 'Hindi'))  # Default language to 'Hindi' if not provided
    session['language'] = language
    print(f"Received tone: {tone}")

    audio_file_name = str(uuid.uuid4()) + ".mp3"

    # Convert both persona name from the request and the stored name to lowercase
    persona = Persona.query.filter(func.lower(Persona.name) == persona_name.lower(),
                                   Persona.user_id == current_user.id).first()

    personas2 = Persona.query.filter_by(user_id=current_user.id).all()
    for p in personas2:
        print(f"Stored persona: {p.name}")

    # Extract relevant persona details
    persona_info = {
        "Name": persona.name,
        "Age": persona.age,
        "Gender": persona.gender,
        "Occupation": persona.occupation,
        "Marital Status": persona.marital_status,
        "Dependent Family Members": persona.dependent_family_members,
        "Financial Goals": persona.financial_goals,
        "Category": persona.category
    }
    print(f"Persona info: {persona_info}")


    persona_gender = persona_info["Gender"]
    print(f"Persona info: {persona_info}")
    print(f"Persona Name: {persona_name}")

    selected_voice = VOICE_MAPPING.get(persona_gender, "hi-IN-SwaraNeural")
    print(f"Selected voice: {selected_voice}")
    print("Tone:", tone)

    language_instruction = "YOU HAVE A CONVERSATION IN HINDI." if language == 'Hindi' else "YOU HAVE A CONVERSATION IN ENGLISH."

    message2 = [
        SystemMessage(
            content=f"""
                CONTEXT: AN INSURANCE AGENT HAS APPROACHED YOU FOR THE FIRST TIME TO SELL AN INSURANCE POLICY.

                YOUR ROLE:
                - ACT AS A POTENTIAL CUSTOMER.
                - FOCUS ON YOUR ROLE AS THE CUSTOMER AND MAINTAIN A CONSISTENT PERSONA THROUGHOUT THE CONVERSATION.
                - YOUR PROFILE: "{persona_name}" AND "{persona_info}".
                - YOUR TONE: "{tone.upper()}".
                - ANSWER ONLY TO WHAT HAS BEEN ASKED RELATED TO CONTEXT.
                - YOU KNOW HINDI AND ENGLISH VERY WELL. {language_instruction}
                - REMEMBER, TAKE A DEEP BREATH AND THINK TWICE BEFORE RESPONDING.
                - KEEP THE CONTEXT OF THE CURRENT CONVERSATION IN MIND AND TAKE IT TOWARDS A POSITIVE END STEP BY STEP BY RESPONDING TO EACH QUERY ONE BY ONE.
                - AVOID RESPONDING AS THE AGENT OR PRODUCING A COMPLETE SCRIPT.
                - KEEP RESPONSES CONCISE AND LIMITED TO A MAXIMUM OF TWO SENTENCES.

                THIS IS VERY IMPORTANT FOR MY CAREER.
                """
        ),
        HumanMessage(content=agent_message),
    ]

    response = await asyncio.to_thread(llm.invoke, message2)
    customer_message = response.content
    print("Customer response: ", customer_message)

    # Azure Text-to-Speech implementation
    speech_config = speechsdk.SpeechConfig(subscription=azure_subscription_key, region=azure_region)
    speech_config.speech_synthesis_voice_name = selected_voice

    audio_config = speechsdk.audio.AudioOutputConfig(filename=f"static/{audio_file_name}")
    speech_synthesizer = speechsdk.SpeechSynthesizer(speech_config=speech_config, audio_config=audio_config)

    result = await asyncio.to_thread(speech_synthesizer.speak_text_async(customer_message).get)
    if result.reason == speechsdk.ResultReason.SynthesizingAudioCompleted:
        print(f"Speech synthesized for text [{customer_message}]")
    elif result.reason == speechsdk.ResultReason.Canceled:
        cancellation_details = result.cancellation_details
        print(f"Speech synthesis canceled: {cancellation_details.reason}")
        if cancellation_details.reason == speechsdk.CancellationReason.Error:
            print(f"Error details: {cancellation_details.error_details}")

    return jsonify({
        "text": customer_message,
        "audio": f"/static/{audio_file_name}",
        "conversation_id": conversation_id
    })


@app.route('/set_tone', methods=['POST'])
@login_required
def set_tone():
    tone = request.json.get('tone', 'polite')  # Default to polite if tone not provided
    session['tone'] = tone
    session.modified = True
    return jsonify({"status": "Tone set successfully"})

@app.route('/add_message', methods=['POST'])
@login_required
def add_message_route():
    try:
        data = request.json
        conversation_id = data.get('conversation_id')
        sender = data.get('sender')
        content = data.get('content')

        if not conversation_id or not sender or not content:
            logging.error(f"Invalid request: conversation_id={conversation_id}, sender={sender}, content={content}")
            return jsonify({'error': 'Invalid request'}), 400

        add_message(conversation_id, sender, content)
        return jsonify({'status': 'Message added'}), 200
    except Exception as e:
        logging.error(f"Error adding message: {e}")
        return jsonify({'error': 'Internal Server Error'}), 500



@app.route('/close_conversation', methods=['POST'])
@login_required
def close_conversation_route():
    data = request.json
    conversation_id = data.get('conversation_id')
    if not conversation_id:
        app.logger.error("conversation_id is required")
        return jsonify({'error': 'conversation_id is required'}), 400

    try:
        feedback = asyncio.run(close_conversation(app, conversation_id))
        if feedback is None:
            app.logger.error("Failed to retrieve feedback for conversation_id: %s", conversation_id)
            return jsonify({'error': 'Failed to retrieve feedback'}), 500
        return jsonify({'status': 'conversation closed', 'feedback': feedback}), 200
    except Exception as e:
        app.logger.error(f"Error in close_conversation_route for conversation_id {conversation_id}: {e}")
        return jsonify({'error': 'Internal server error'}), 500



@app.route('/clear_session', methods=['POST'])
@login_required
def clear_session():
    session.pop('conversation_id', None)
    session.modified = True
    return jsonify({'status': 'Session cleared'}), 200








@app.route('/remove_all_audio_files', methods=['POST'])
async def remove_all_audio_files():
    try:
        audio_files = glob.glob(os.path.join("static", "*.mp3"))  # Adjust the extension if needed
        for file_path in audio_files:
            await asyncio.to_thread(os.remove, file_path)
        return jsonify({"message": "All audio files removed successfully"})
    except Exception as e:
        print("Error deleting audio files:", e)
        return jsonify({"error": "Failed to remove audio files"}), 500

#---------------------------------------------------------------------------------------------------------------------------
# Reflect





if __name__ == "__main__":
    import os
    port = int(os.environ.get("PORT", 8000))
    app.run(host="0.0.0.0", port=port)
