from flask import Blueprint, render_template, request, jsonify
from PyPDF2 import PdfReader
from langchain.text_splitter import RecursiveCharacterTextSplitter
import os
from langchain_google_genai import GoogleGenerativeAIEmbeddings
import google.generativeai as genai
from langchain_community.vectorstores import FAISS
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain.chains.question_answering import load_qa_chain
from langchain.prompts import PromptTemplate
from dotenv import load_dotenv
from flask_login import login_required, current_user

knowledge_bp = Blueprint("recall", __name__, url_prefix="/recall")

load_dotenv()
os.getenv("GOOGLE_API_KEY")
genai.configure(api_key=os.getenv("GOOGLE_API_KEY"))


def get_pdf_text(pdf_docs):
    text = ""
    for pdf in pdf_docs:
        try:
            pdf_reader = PdfReader(pdf)
            for page in pdf_reader.pages:
                text += page.extract_text()
        except Exception as e:
            print(f"Error reading PDF file: {e}")
    return text


def get_text_chunks(text):
    text_splitter = RecursiveCharacterTextSplitter(chunk_size=10000, chunk_overlap=1000)
    chunks = text_splitter.split_text(text)
    return chunks


def get_vector_store(text_chunks):
    embeddings = GoogleGenerativeAIEmbeddings(model="models/embedding-001")
    vector_store = FAISS.from_texts(text_chunks, embedding=embeddings)
    vector_store.save_local("faiss_index")


def get_conversational_chain():
    prompt_template = """
    You are proficient in Hindi. Answer the question(s) in a detailed and well-structured manner based on the provided context.
    If there are multiple questions, separate your answers clearly, starting each new answer on a new line with a line space before it.
    Ensure that each section is well-separated and clearly labeled if appropriate.
    Organize the response into clear paragraphs, and include bullet points if necessary. 
    If the answer is not found in the provided context, simply state, "उत्तर संदर्भ में उपलब्ध नहीं है" (answer is not available in the context). 
    Do not provide any incorrect information.

    संदर्भ (Context):\n {context}\n
    प्रश्न (Question):\n{question}\n

    उत्तर (Answer):
    """

    model = ChatGoogleGenerativeAI(model="gemini-pro", temperature=0.3)

    prompt = PromptTemplate(template=prompt_template, input_variables=["context", "question"])
    chain = load_qa_chain(model, chain_type="stuff", prompt=prompt)

    return chain




@knowledge_bp.route("/", methods=["GET"])
@login_required
def render_knowledge_page():
    return render_template("recall.html")


@knowledge_bp.route("/upload", methods=["POST"])
@login_required
def upload_document():
    pdf_docs = request.files.getlist("pdf_docs")
    if pdf_docs:
        print(pdf_docs)
        raw_text = get_pdf_text(pdf_docs)
        text_chunks = get_text_chunks(raw_text)
        get_vector_store(text_chunks)
        message = "Document uploaded..."
        return render_template("recall.html", message=message)
    else:
        return "No PDF files uploaded."


@knowledge_bp.route("/ask", methods=["POST"])
def ask_question():
    try:
        data = request.get_json()
        user_question = data.get("question", "")
        if user_question:
            response = user_input(user_question)
            return jsonify(response=response["output_text"])
        else:
            return jsonify(response="No question provided"), 400
    except Exception as e:
        print(f"Error handling request: {e}")
        return jsonify(response="Internal Server Error"), 500


def user_input(user_question):
    # Load embeddings and FAISS index
    embeddings = GoogleGenerativeAIEmbeddings(model="models/embedding-001")
    new_db = FAISS.load_local("faiss_index", embeddings, allow_dangerous_deserialization=True)

    # Perform similarity search to get relevant documents
    docs = new_db.similarity_search(user_question)

    # Combine document contents into a single context string
    context = " ".join([doc.page_content.replace("\n", " ").strip() for doc in
                        docs])  # Remove line breaks and strip unnecessary spaces

    # Fetch the conversational chain
    chain = get_conversational_chain()

    # Use `invoke` instead of `__call__` or `run`
    response = chain.invoke({"input_documents": docs, "question": user_question})

    # Handle the response text to ensure proper formatting
    formatted_response = response["output_text"].replace("\n", " ").replace("  ", " ").replace("**", "<b>").replace("*",
                                                                                                                    "<li>")

    return {"output_text": formatted_response}




