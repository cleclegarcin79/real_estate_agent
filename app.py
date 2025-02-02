__import__('pysqlite3')
import sys
sys.modules['sqlite3'] = sys.modules.pop('pysqlite3')

import streamlit as st
from langchain_community.document_loaders import PyPDFLoader
from langchain_openai import ChatOpenAI, OpenAIEmbeddings
from langchain.prompts import ChatPromptTemplate
from langchain_community.vectorstores import Chroma
import chromadb
from chromadb.utils.embedding_functions import OpenAIEmbeddingFunction
from langchain.text_splitter import RecursiveCharacterTextSplitter
from langchain.chains.combine_documents import create_stuff_documents_chain
from langchain.chains.retrieval import create_retrieval_chain

# Page setting
st.set_page_config(layout="wide")

# Replace it with your OPENAI API KEY
OPENAI_API_KEY = st.secrets["OPENAI_API_KEY"]

collection_name="chat-with-pdf"

# Load Vector datasse
native_db = chromadb.PersistentClient("./chroma_db")
db = Chroma(client=native_db, collection_name=collection_name, embedding_function=OpenAIEmbeddings())



# Init langchain
llm = ChatOpenAI(
    model="gpt-4o-mini",
    temperature=0,
    max_tokens=None,
    timeout=None,
    max_retries=2,
    api_key=OPENAI_API_KEY)

embeddings = OpenAIEmbeddingFunction(
    model_name="text-embedding-3-small",
    api_key=OPENAI_API_KEY)

prompt = ChatPromptTemplate.from_template("""
Based on the provided context only, find the best answer for my question. Format the answer in markdown format
<context>
{context}
</context>
Question:{input}
""")
document_chain = create_stuff_documents_chain(llm, prompt)
retriever = db.as_retriever()
retriever_chain = create_retrieval_chain(retriever, document_chain)

if "question" not in st.session_state:
    st.session_state.question = None

if "old_filenames" not in st.session_state:
    st.session_state.old_filenames = []


@st.cache_resource
def get_collection():
    print("DEBUG: call get_collection()")
    collection = None
    try:
        # Delete all documents
        native_db.delete_collection(collection_name)
    except:
        pass
    finally:
        collection = native_db.get_or_create_collection(collection_name,
                                                        embedding_function=embeddings)
    return collection


# Load, transform and embed new files into Vector Database
def add_files(uploaded_files):
    collection = get_collection()

    # old_filenames: contains a list of names of files being used
    # uploaded_filenames: contains a list of names of uploaded files
    old_filenames = st.session_state.old_filenames
    uploaded_filename = [file.name for file in uploaded_files]
    new_files = [file for file in uploaded_files if file.name not in old_filenames]

    for file in new_files:
        # Step 1: load uploaded file
        # save doc temporary
        temp_file = f"./{file.name}"
        with open(temp_file, "wb") as f:
            f.write(file.getvalue())
        loader = PyPDFLoader(temp_file)
        pages = loader.load()

        # Step 2: split content in to chunks
        text_splitter = RecursiveCharacterTextSplitter(separators="\n",chunk_size=500, chunk_overlap=50)
        chunks = text_splitter.split_documents(pages)

        # Step 3: embed chunks into Vector Store
        # collection.add(ids=file.name,documents=chunks)
        for index, chunk in enumerate(chunks):
            collection.upsert(
                ids=[chunk.metadata.get("source") + str(index)], metadatas=chunk.metadata,
                documents=chunk.page_content
            )


# Remove all relevant chunks of the removed files
def remove_files(uploaded_files):
    collection = get_collection()

    # old_filenames: contains a list of names of files being used
    # uploaded_filenames: contains a list of names of uploaded files
    old_filenames = st.session_state.old_filenames
    uploaded_filename = [file.name for file in uploaded_files]

    # Step 1: Get the list of file that was removed from upload files
    deleted_filenames = [name for name in old_filenames if name not in uploaded_filename]

    # Step 2: Remove all relevant chunks of the removed files
    if len(deleted_filenames) > 0:
        all_chunks = collection.get()

        ids = all_chunks["ids"]
        metadatas = all_chunks["metadatas"]

        if len(metadatas) > 0:
            deleted_ids = []
            for name in deleted_filenames:
                for index, metadata in enumerate(metadatas):
                    if metadata['source'] == f"./temp/{name}.pdf":
                        deleted_ids.append(ids[index])
            collection.delete(ids=deleted_ids)


# Return chunks after having any change in the file list
def refresh_chunks(uploaded_files):
    # old_filenames: contains a list of names of files being used
    # uploaded_filenames: contains a list of names of uploaded files
    old_filenames = st.session_state.old_filenames
    uploaded_filename = [file.name for file in uploaded_files]

    if len(old_filenames) < len(uploaded_filename):
        add_files(uploaded_files)
    elif len(old_filenames) > len(uploaded_filename):
        remove_files(uploaded_files)

    # Step 3: Save the state
    st.session_state.old_filenames = uploaded_filename


if st.button("Refresh"):
    # Clears all st.cache_resource caches:
    st.cache_resource.clear()

st.header("📗 Chat with PDF (RAG version)")

uploaded_files = st.file_uploader("Choose a PDF", accept_multiple_files=True, type="pdf",
                                label_visibility="collapsed")
refresh_chunks(uploaded_files)

col1, col2 = st.columns([4, 6])
collection = get_collection()
chunk_count = collection.count()
with col1:
    st.write(f"TOTAL CHUNKS:{chunk_count}")
    if st.session_state.question is not None:
        relevant_chunk = retriever.invoke(input=st.session_state.question)
        st.write("RELEVANT CHUNKS:")
        st.write(relevant_chunk)
    else:
        all_chunks = collection.get()
        st.write(all_chunks)
with col2:
    if chunk_count > 0:
        query = st.text_input(label="Question", placeholder="Please ask me anything related to your files",
                            value="")
        ask = st.button("Send message", type="primary")
        if len(query) > 0:
            with st.spinner("Sending message....."):
                st.session_state.question = query
                if ask:
                    response = retriever_chain.invoke({"input": query})
                    st.write(response['answer'])