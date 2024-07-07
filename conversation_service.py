# conversation_service.py
from datetime import datetime
from flask_login import current_user
from extensions import db
from models import Conversation, Message, Feedback
from langchain_google_genai import ChatGoogleGenerativeAI
import os
import google.generativeai as genai
from dotenv import load_dotenv
import asyncio

load_dotenv()

api_key=os.environ['GOOGLE_API_KEY']
genai.configure(api_key=api_key)
llm = ChatGoogleGenerativeAI(model="gemini-pro", convert_system_message_to_human=True)

def start_conversation(user_id, persona):
    conversation = Conversation(user_id=user_id, persona=persona)
    db.session.add(conversation)
    db.session.commit()
    return conversation.id




def add_message(conversation_id, sender, content):

    message = Message(conversation_id=conversation_id, sender=sender, content=content)
    db.session.add(message)
    db.session.commit()



async def generate_feedback(conversation):
    if not conversation or not conversation.messages:
        return "Feedback could not be generated due to missing conversation details."

    formatted_conversation = ""
    for message in conversation.messages:
        sender = "Customer" if message.sender == 'system' else "Agent"
        formatted_conversation += f"{sender}: {message.content}\n"
        print(f"Processing message: sender={sender}, content={message.content}")  # Debugging line

    overall_prompt = (
        "Based on the following conversation between an insurance agent and a customer, provide feedback in a mix of Hindi and English on the agent's performance. "
        "The feedback should be categorized as either 'Positives' or 'Needs Improvement' only if necessary and include specific comments on how the agent handled the conversation. "
        "Consider the overall chat conversation in context. Do not generate or write '***' in feedback text. These categories should be in English.\n\n"
        f"Conversation:\n{formatted_conversation}\n\nOverall Feedback:"
    )

    overall_response = await llm_invoke(overall_prompt)
    overall_feedback = overall_response.content if overall_response else "Could not generate feedback at this time."

    individual_feedback_list = []
    for message in conversation.messages:
        if message.sender == 'user':
            individual_prompt = (
                "Provide feedback in a mix of Hindi and English on the following response from the agent. specially those english words which are better understood in english. "
                "Indicate whether it was 'Positive' or 'Needs Improvement' only if necessary and provide specific comments on how it could be improved if needed. "
                "Consider the overall chat conversation in context. Do not generate '***' in feedback text. These indicators should be in English.\n\n"
                f"Agent's response: {message.content}\n\nFeedback:"
            )

            individual_response = await llm_invoke(individual_prompt)
            feedback_text = individual_response.content if individual_response else "Could not generate individual feedback at this time."
            individual_feedback_list.append(f"Agent's response: {message.content}\nFeedback: {feedback_text}")

    combined_feedback = f"Overall Feedback:\n{overall_feedback}\n\nIndividual Feedback:\n" + "\n\n".join(individual_feedback_list)

    return combined_feedback




async def llm_invoke(prompt):
    response = await asyncio.to_thread(llm.invoke, prompt)
    return response


async def close_conversation(conversation_id):
    conversation = Conversation.query.get(conversation_id)
    if not conversation:
        return "No conversation found with the given ID."

    existing_feedback = Feedback.query.filter_by(conversation_id=conversation_id).first()
    if existing_feedback:
        return existing_feedback.content  # Return the existing feedback if it exists

    try:
        feedback_content = await generate_feedback(conversation)
        feedback = Feedback(conversation_id=conversation_id, content=feedback_content)
        db.session.add(feedback)
        db.session.commit()
        return feedback_content
    except Exception as e:
        db.session.rollback()
        return f"An error occurred while closing the conversation: {str(e)}"






