from flask import request, jsonify, session
from flask_login import login_required, current_user
import random, uuid
from datetime import datetime
import azure.cognitiveservices.speech as speechsdk
import asyncio
from models import Product, ReferConversation, Conversation, ReferFeedback, ReferMessage  # Adjust based on your project structure
from flask import Blueprint
import os
import google.generativeai as genai
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_google_genai import GoogleGenerativeAIEmbeddings
from langchain_google_genai import ChatGoogleGenerativeAI
from flask import current_app
from extensions import login_manager, csrf, mail, oauth, db

# Blueprints
reflect_bp = Blueprint('reflect', __name__)

# API Keys from environment file
api_key = os.getenv('GOOGLE_API_KEY')
servamapi_key = os.getenv('SERVAM_API_KEY')
azure_subscription_key = os.getenv("AZURE_SUBSCRIPTION_KEY")
azure_region = os.getenv("AZURE_REGION")

# LLM Configuration
genai.configure(api_key=api_key)
llm = ChatGoogleGenerativeAI(model="gemini-pro", convert_system_message_to_human=True, temperature=0.8)

# Voice mappings for personas
VOICE_MAPPING = {
    "Male": "hi-IN-MadhurNeural",
    "Female": "hi-IN-SwaraNeural"
}

# Route to add refer message to conversation
@reflect_bp.route('/add_refer_message', methods=['POST'])
@login_required
def add_refer_message():
    data = request.json
    current_app.logger.info(f"Request Data: {data}")

    conversation_id = data.get('conversation_id')
    sender = data.get('sender')
    content = data.get('content')

    if not conversation_id or not sender or not content:
        current_app.logger.error("Invalid request: Missing conversation_id, sender, or content")
        return jsonify({'error': 'Invalid request'}), 400

    #add_message(conversation_id, sender, content)
    return jsonify({'status': 'Message added'}), 200


@reflect_bp.route('/reset_session', methods=['POST'])
@login_required
def reset_session():
    # Clear session data related to the conversation
    session.pop('conversation_id', None)
    session.pop('shuffled_questions', None)
    session.pop('questions_asked', None)
    session.pop('correct_answers', None)
    session.modified = True
    return jsonify({"status": "Session reset successfully"})



# Route to load products for selection
@reflect_bp.route('/load-products', methods=['GET'])
@login_required
def load_products():
    try:
        products = db.session.query(Product.name).distinct().all()
        product_list = [{"name": product[0]} for product in products]
        return jsonify({"products": product_list})
    except Exception as e:
        current_app.logger.error(f"Error loading products: {e}")
        return jsonify({"error": "Failed to load products"}), 500

# Function to get questions and answers for the selected product
def get_product_questions(product_name, language):
    if language == "Hindi":
        products = Product.query.filter_by(name=product_name).with_entities(Product.question_hindi, Product.answer_hindi).all()
    else:
        products = Product.query.filter_by(name=product_name).with_entities(Product.question_english, Product.answer_english).all()

    product_questions = []
    product_answers = []
    for question, answer in products:
        product_questions.append(question)
        product_answers.append(answer)

    return product_questions, product_answers # Returning two lists, one for questions, one for answers



# Function to get the correct answer for a given question
def get_correct_answer(product_name, current_question, language):
    if language == "Hindi":
        correct_answer_row = Product.query.filter_by(name=product_name, question_hindi=current_question).first()
        if correct_answer_row:
            return correct_answer_row.answer_hindi
    else:
        correct_answer_row = Product.query.filter_by(name=product_name, question_english=current_question).first()
        if correct_answer_row:
            return correct_answer_row.answer_english
    return None

# Main conversation handler
# Main conversation handler
@reflect_bp.route('/conversation/<string:product_name>', methods=['POST'])
@login_required
async def manage_conversation(product_name):
    try:
        # Step 1: Get the action and other parameters from the request
        action = request.json.get('action', 'answer')  # Default action is to provide feedback for the answer
        language = request.json.get('language', session.get('language', 'Hindi'))
        session['language'] = language
        user_answer = request.json.get('user_transcript')


        # Step 2: Check if it's a new conversation
        conversation_id = session.get('conversation_id')


        # Log incoming request details
        current_app.logger.info(f"Received conversation ID: {conversation_id}, User Answer: {user_answer}, Action: {action}")

        if not conversation_id:
            # Initialize a new conversation
            conversation_id = initialize_refer_conversation(current_user.id, product_name)
            session['conversation_id'] = conversation_id
            session['score'] = 0
            session['questions_asked'] = 0
            session['correct_answers'] = 0

            # If there's user input at the start, save it as the first message
            if user_answer:
                add_refer_message(conversation_id, sender='user', content=user_answer)

            # Fetch both questions and their corresponding answers based on language
            if language == "Hindi":
                product_questions = Product.query.filter_by(name=product_name).with_entities(
                    Product.question_hindi, Product.answer_hindi).all()
            else:
                product_questions = Product.query.filter_by(name=product_name).with_entities(
                    Product.question_english, Product.answer_english).all()

            if not product_questions:
                return jsonify({"error": "No questions found for the selected product."}), 404

            # Shuffle the questions while keeping answers paired
            random.shuffle(product_questions)

            # Store both questions and answers in session for easy retrieval
            session['shuffled_questions'] = [{'question': q[0], 'answer': q[1]} for q in product_questions]
            session['total_questions'] = min(10, len(session['shuffled_questions']))  # Set total to 10

            # Get the first question
            current_question = session['shuffled_questions'][0]['question']
            session['current_question'] = current_question
            session['questions_asked'] = 1
            session.modified = True

            # AI Coach greeting and context setting based on selected language
            if session['language'] == "Hindi":
                """
                hindi_greetings = [
                    f"नमस्ते! मैं आज आपका कोच हूँ। हम साथ में {product_name} के बारे में आपके ज्ञान को समझेंगे। कोई चिंता की बात नहीं, मैं यहाँ आपकी मदद के लिए हूँ। यह रहा आपका पहला प्रश्न।",
                    f"नमस्कार! आज हम {product_name} के बारे में आपकी समझ का परीक्षण करेंगे। मैं आपके साथ हूँ और हर कदम पर आपका मार्गदर्शन करूंगा। तो, शुरू करते हैं। यहाँ पहला प्रश्न है।",
                    f"आपका स्वागत है! मैं आपका कोच हूँ और आज हम {product_name} से जुड़ी कुछ बातें जानेंगे। आप तैयार हैं? तो चलिए शुरू करते हैं, यह रहा पहला सवाल।",
                    f"नमस्ते! मैं यहाँ हूँ आपकी मदद के लिए, ताकि हम मिलकर {product_name} के बारे में आपकी जानकारी को सुधारें। कोई भी संकोच मत कीजिए, यह रहा आपका पहला प्रश्न।",
                    f"नमस्कार! आज हम {product_name} पर आधारित आपके ज्ञान का मूल्यांकन करेंगे। चिंता मत कीजिए, मैं आपके साथ हूँ। शुरू करते हैं, यह रहा पहला सवाल।"
                ]
                """
                hindi_greetings = [
                    f"नमस्ते! "
                ]
                # Randomly select a greeting
                coach_greeting = random.choice(hindi_greetings)
                question_prompt = current_question
            else:
                english_greetings = [
                    f"Hello! I'm your coach today. Let’s explore your knowledge of {product_name}. Don’t worry, I’m here to guide you. Here’s your first question.",
                    f"Welcome! I'm here to help you test your understanding of {product_name}. Ready? Let’s dive in. Here's the first question for you.",
                    f"Hello! Let’s work together to assess your knowledge of {product_name}. No need to worry, I’ll be right here to assist. Here's your first question.",
                    f"Greetings! I'm your coach today, and we’ll be covering {product_name}. Don’t worry, I’ll guide you through it step by step. Let's begin with the first question.",
                    f"Hi! I’m here to guide you through a quick test of your knowledge on {product_name}. I’ll be with you throughout. Here’s your first question."
                ]
                # Randomly select a greeting
                coach_greeting = random.choice(english_greetings)
                question_prompt = current_question

            # Synthesize and return the first question with audio
            conversation_context = f"{coach_greeting}\n{question_prompt}"
            add_refer_message(conversation_id, sender='Question', content=question_prompt)

            current_app.logger.info(f"Generating speech for: {conversation_context}")
            audio_file_name = await synthesize_speech(conversation_context, language)
            current_app.logger.info(f"Audio file generated: {audio_file_name}")

            if not audio_file_name:
                current_app.logger.error(f"Error generating audio for question prompt: {conversation_context}")
                return jsonify({"error": "Failed to generate audio for the conversation."}), 500

            return jsonify({
                "text": current_question,
                "audio": f"/static/{audio_file_name}",
                "conversation_id": conversation_id
            })

            # Step 3: Handle actions for an ongoing conversation
        if action == 'answer':

            # Provide feedback for the current answer
            if 'shuffled_questions' not in session or 'questions_asked' not in session:
                current_app.logger.error("Session data missing: shuffled_questions or questions_asked")
                return jsonify({"error": "Session data is missing."}), 400

            current_question_index = session['questions_asked'] - 1

            # Ensure the index is within bounds
            if current_question_index >= len(session['shuffled_questions']):
                return jsonify({"error": "No more questions available."}), 400

            current_qa_pair = session['shuffled_questions'][current_question_index]
            correct_answer = current_qa_pair['answer']

            current_app.logger.info(
                f"Current question index: {current_question_index}, Correct Answer: {correct_answer}")

            prompt = [
                SystemMessage(
                    content=f"""
                        You are tasked with correcting **only** the misspelled words, grammatical errors, or mispronounced words in the user's answer. 

                        **Important Instructions**:
                        - **Do not change the meaning** or rephrase the sentence.
                        - **Do not replace the user's original answer with the correct answer**; just correct specific mistakes.
                        - **Focus on key terms that might be misheard or mispronounced** (e.g., technical terms like "Single Pay", "Limited Pay", "Regular Pay", "Deferment", "Assured Wealth Goal plan", "surrender value", "premiums", "Life Stage", "Sum Assured", "terminal", "lump sum", "Life Shield", "Death Benefit", "Pay" or "Regular" that might be critical to understanding the context).
                        - Preserve the overall phrasing and structure of the user's original answer as much as possible.

                        Here is the user's answer: "{user_answer}"

                        Compare it with the context of the correct answer: "{correct_answer}"

                        Your job is to:
                        - Correct only spelling, grammar, and pronunciation errors.
                        - Ensure important key terms, like product names or financial terms, are correctly spelled (e.g., "Single Pay" should not be changed to "Single Phase").

                        Do not rephrase or restructure the user's original response, but ensure the corrections maintain clarity and accuracy.
                    """
                ),
                HumanMessage(content=user_answer)
            ]

            # AI LLM call to generate a human-like conversational response
            response = await asyncio.to_thread(llm.invoke, prompt)
            user_answer1 = response.content
            user_answer2 = user_answer1.replace('*', '')
            current_app.logger.info(f"user_answer 02: {user_answer2}")
            current_app.logger.info(f"language: {language}")
            # Log the user's response
            add_refer_message(conversation_id, sender='user', content=user_answer2)
            # Generate feedback for the current question
            feedback_text = await get_coach_feedback(user_answer2, correct_answer, language)
            add_refer_message(conversation_id, sender='AI', content=feedback_text)

            # Update session score based on the feedback
            if "आपका उत्तर सही है" in feedback_text.lower() or "your answer is correct" in feedback_text.lower():
                session['correct_answers'] += 1
            elif "आपका उत्तर आंशिक रूप से सही है" in feedback_text.lower() or "your answer is partially correct" in feedback_text.lower():
                session['correct_answers'] += 0.5

            session.modified = True  # Ensure session update is persisted
            current_app.logger.info(f"Coach feedback: {feedback_text}")
            current_app.logger.info(f"Coach Score: {session['correct_answers']}")

            feedback_audio_file_name = await synthesize_speech(feedback_text, language)
            if not feedback_audio_file_name:
                current_app.logger.error(f"Error generating audio for feedback: {feedback_text}")
                return jsonify({"error": "Failed to generate audio for feedback."}), 500

            return jsonify({
                "feedback_text": feedback_text,
                "feedback_audio": f"/static/{feedback_audio_file_name}",
                "correct_answer": correct_answer,
                "user_answer2": user_answer2,
                "conversation_id": conversation_id,
                "is_final_feedback": False
            })

        elif action == 'next_question':
            # Provide the next question if available

            session['questions_asked'] += 1
            session.modified = True  # Ensure session update is persisted

            # Enforce the limit of 10 questions
            if session['questions_asked'] > session['total_questions']:
                # No more questions available, return final feedback
                final_score = session['correct_answers']
                final_feedback = generate_feedback(final_score, session['total_questions'])
                final_feedback_audio_file_name = await synthesize_speech(final_feedback, language)

                # Add this code here to save the final feedback to the database
                final_feedback_text = final_feedback
                try:
                    feedback_entry = ReferFeedback(
                        conversation_id=conversation_id,
                        content=final_feedback_text,
                        score=final_score,
                        category=final_feedback,
                        timestamp=datetime.utcnow()
                    )
                    db.session.add(feedback_entry)
                    db.session.commit()
                    current_app.logger.info("Feedback saved successfully in the database.")
                except Exception as e:
                    db.session.rollback()
                    current_app.logger.error(f"Error saving feedback to the database: {e}")

                return jsonify({
                    "final_feedback_text": final_feedback,
                    "final_feedback_audio": f"/static/{final_feedback_audio_file_name}",
                    "conversation_id": conversation_id,
                    "is_final_feedback": True
                })

            # If more questions are available, serve the next question
            next_question_index = session['questions_asked'] - 1
            # Ensure the next question index is valid
            if next_question_index >= len(session['shuffled_questions']):
                return jsonify({"error": "No more questions available."}), 400

            # Get the next question
            next_question = session['shuffled_questions'][next_question_index]['question']
            add_refer_message(conversation_id, sender='Question', content=next_question)
            next_question_audio_file_name = await synthesize_speech(next_question, language)

            return jsonify({
                "next_question_text": next_question,
                "next_question_audio": f"/static/{next_question_audio_file_name}",
                "conversation_id": conversation_id,
                "is_final_feedback": False
            })

    except IndexError as e:
        current_app.logger.error(f"IndexError in conversation: {str(e)}")
        return jsonify({"error": "Index out of range"}), 500
    except Exception as e:
        current_app.logger.error(f"Error in conversation: {str(e)}")
        return jsonify({"error": str(e)}), 500



# Utility functions for AI feedback and speech synthesis
async def get_coach_feedback(user_answer2, correct_answer, language):
    # Function to generate AI feedback based on the user's answer
    if language == "Hindi":
        prompt = [
            SystemMessage(
                content=f"""
                        You are a professional question paper evaluator evaluating the user's answer. 
                        Use colloquial Hindi language. Address the user as 'आप'.                       

                        The user's answer is: "{user_answer2}". The correct answer is: "{correct_answer}".

                        Compare the "{user_answer2}" with the "{correct_answer}" and determine whether the meaning of 
                        "{user_answer2}" and "{correct_answer}" is similar and "{user_answer2}" covers the 
                        key concepts of "{correct_answer}".
                        
                        IGNORE GRAMMATICAL MISTAKES ANS MISSPELLED WORDS WHILE COMPARING.                     

                        If the "{user_answer2}" is similar in meaning to the "{correct_answer}" and covers most key 
                        concepts, respond with 'आपका उत्तर सही है'. 
                        
                        If the "{user_answer2}" is partially similar in meaning to the "{correct_answer}" and covers 
                        some key concepts, say 'आपका उत्तर आंशिक रूप से सही है'. 
                        
                        If the "{user_answer2}" is not similar in meaning to the "{correct_answer}" and misses
                        important details, say 'आपका उत्तर गलत है'.


                        If it is incomplete or incorrect, explain the correct answer briefly and encourage the user 
                        to move forward.
                        """
            ),
            HumanMessage(content=user_answer2),
        ]
    elif language == "English":
        prompt = [
            SystemMessage(
                content=f"""
                    You are a professional evaluator assessing the user's spoken answer, which has been converted into text.
                    Your primary goal is to assess whether the user's answer conveys the correct **key concepts** and overall meaning of the correct answer.

                    **User's answer**: "{user_answer2}"
                    **Correct answer**: "{correct_answer}"

                    **Important Instructions**:
                    - Focus on whether the user's answer includes all the key concepts from the correct answer.
                    - Be lenient with phrasing, wording, or minor structural differences, as long as the overall meaning is the same.
                    - If the user's answer covers the core concepts, even if phrased differently, respond with 'Your answer is correct'.
                    - If the user's answer is missing one or two important details, respond with 'Your answer is partially correct' and briefly explain what was missed.
                    - If the user's answer is significantly incorrect or missing key concepts, respond with 'Your answer is incorrect' and explain what was missed.

                    **Evaluation Criteria**:
                    - Do not penalize for minor variations in how the user phrases their answer, as long as the meaning is clear.
                    - If the user covers the main points of the correct answer, treat the answer as correct.

                    REMEMBER: Take a deep breath and read the user's answer carefully before responding. Your evaluation will have a significant impact on the user's professional development, so ensure your judgment is fair and lenient where appropriate.
                """
            ),
            HumanMessage(content=user_answer2),
        ]

    response = await asyncio.to_thread(llm.invoke, prompt)
    response_clean = response.content
    correct_answer_clean = response_clean.replace('*', '')

    return correct_answer_clean


async def synthesize_speech(text, language):
    selected_voice = VOICE_MAPPING["Male"] if language == "Hindi" else "en-IN-PrabhatNeural"
    speech_config = speechsdk.SpeechConfig(subscription=azure_subscription_key, region=azure_region)
    speech_config.speech_synthesis_voice_name = selected_voice
    audio_file_name = str(uuid.uuid4()) + ".mp3"
    audio_config = speechsdk.audio.AudioOutputConfig(filename=f"static/{audio_file_name}")
    speech_synthesizer = speechsdk.SpeechSynthesizer(speech_config=speech_config, audio_config=audio_config)
    result = await asyncio.to_thread(speech_synthesizer.speak_text_async(text).get)
    return audio_file_name if result.reason == speechsdk.ResultReason.SynthesizingAudioCompleted else None


# Helper function to generate feedback based on the score
def generate_feedback(score, total_questions):
    score_percentage = (score / total_questions) * 100
    score = f"Score: {score}/{total_questions} ({score_percentage:.2f}%)"

    if score_percentage >= 90:
        category = "Expert"
        feedback_text = f"Excellent job! You have a deep understanding of the product and effectively communicated its benefits. Keep up the great work!\n{score}"
    elif score_percentage >= 60:
        category = "Proficient"
        feedback_text = f"Good effort! You have a solid grasp of the product but there are some areas where you can improve. Focus on refining your knowledge.\n{score}"
    elif score_percentage >= 25:
        category = "Competent"
        feedback_text = f"You’re on the right track, but there’s more to learn. Review the product details and practice to enhance your knowledge.\n{score}"
    else:
        category = "Beginner"
        feedback_text = f"You need to learn and practice more. Take time to study the product thoroughly and seek guidance to improve your understanding.\n{score}"

    return f"Category: {category}\nFeedback: {feedback_text}"


# Initialize a new conversation in the database
def initialize_refer_conversation(user_id, product_name):
    conversation = ReferConversation(user_id=user_id, product_id=product_name)
    db.session.add(conversation)
    db.session.commit()
    return conversation.id

def add_refer_message(conversation_id, sender, content):
    message = ReferMessage(conversation_id=conversation_id, sender=sender, content=content)
    db.session.add(message)
    db.session.commit()



