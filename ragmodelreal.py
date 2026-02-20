import ollama
import chromadb
from pypdf import PdfReader
import os

INPUT_DIR = './subject'
TARGET_DIR = './output'
MODEL = 'mannix/llama3.1-8b-abliterated:latest'
EMBED_MODEL = 'nomic-embed-text'

client = chromadb.PersistentClient(path="./rag_storage")
collection = client.get_or_create_collection(name="generic_knowledge")

def sync_knowledge():
    existing_ids = collection.get()['ids']
    
    for filename in os.listdir(INPUT_DIR):
        if filename.endswith(".pdf") and f"{filename}_chunk_0" not in existing_ids:
            print(f"Indexing {filename}...")
            reader = PdfReader(os.path.join(INPUT_DIR, filename))
            
            full_text = ""
            for page in reader.pages:
                full_text += page.extract_text() + "\n"

            chunk_size = 1000
            chunks = [full_text[i:i+chunk_size] for i in range(0, len(full_text), chunk_size)]

            for i, chunk in enumerate(chunks):
                embed = ollama.embeddings(model=EMBED_MODEL, prompt=chunk)['embedding']
                collection.add(
                    ids=[f"{filename}_chunk_{i}"],
                    embeddings=[embed],
                    documents=[chunk]
                )

def auto_fill_templates():
    if collection.count() == 0:
        print("Err. go figure it out urself lol.")
        return

    for filename in os.listdir(TARGET_DIR):
        if filename.endswith(".txt"):
            task_name = filename.replace('.txt', '').replace('_', ' ')
            print(f"PROCESSING {task_name}...")

            query_embed = ollama.embeddings(model=EMBED_MODEL, prompt=task_name)['embedding']
            results = collection.query(query_embeddings=[query_embed], n_results=5)
            context = "\n\n".join(results['documents'][0])

            prompt = f"""
            [SYSTEM: MANDATORY INSTRUCTION]
            Output ONLY the raw text content. 
            NO introductions, NO explanations, NO 'Here is the content', NO 'Sure thing'.
            If you include any conversational text, the task fails.

            [TASK]
            Based on the context below, write the content for {filename}. 
            Format: Academic Topic Outline (List only).

            [CONTEXT]
            {context}

            [OUTPUT START]
            """
            response = ollama.generate(model=MODEL, prompt=prompt)
            
            with open(os.path.join(TARGET_DIR, filename), 'w') as f:
                f.write(response['response'].strip())
                print(f"WRITTEN {filename}")
def x(task_filename):
    query_text = "core technical concepts and main learning objectives"
    query_embed = ollama.embeddings(model=EMBED_MODEL, prompt=query_text)['embedding']
    results = collection.query(query_embeddings=[query_embed], n_results=10)
    context = "\n\n".join(results['documents'][0])

    task_name = task_filename.replace('.txt', '')

    prompt = f"""
    [TECHNICAL DATA]
    {context}

    [TASK]
    You are an expert academic auditor. The provided data is raw technical information.
    Your job is to DEVELOP a {task_name.upper()} based on this data.
    
    - If it's a SYLLABUS: Structure the technical data into a logical 5-unit learning path.
    - If it's CO-PO MAPPING: Analyze the technical skills in the data and map them to Program Outcomes (Knowledge, Analysis, Design).
    
    [STRICT RULE]
    Do not search for these labels in the data. INVENT them based on the technical content.
    Output ONLY the content for the file. No preamble.
    """

    response = ollama.generate(model=MODEL, prompt=prompt)
    return response['response'].strip()

if __name__ == "__main__":
    sync_knowledge()
    auto_fill_templates()
    for filename in os.listdir(TARGET_DIR):
        if filename.endswith(".txt"):
            task_name = filename.replace('.txt', '').replace('_', ' ')
            print(f"PROCESSING {task_name}...")
            with open(os.path.join(TARGET_DIR, filename), 'w') as f:
                        f.write(x(filename))
                        print(f"WRITTEN {task_name}...")

