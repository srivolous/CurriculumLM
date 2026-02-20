import ollama
import os
from pypdf import PdfReader

INPUT_DIR = './subject'
TARGET_DIR = './output'
MODEL = 'mannix/llama3.1-8b-abliterated:latest'

def get_content():
    combined_text = ""
    for filename in os.listdir(INPUT_DIR):
        path = os.path.join(INPUT_DIR, filename)
        
        if filename.endswith(".pdf"):
            print(f"Reading: {filename}...")
            reader = PdfReader(path)
            for page in reader.pages:
                combined_text += page.extract_text() + "\n"
        
        elif filename.endswith(".txt"):
            print(f"Reading: {filename}...")
            with open(path, 'r') as f:
                combined_text += f.read() + "\n"
                
    return combined_text

def fill_templates():
    source_data = get_content()
    
    if not source_data.strip():
        print("Error. go figure it out urself lol.")
        return

    for filename in os.listdir(TARGET_DIR):
        if filename.endswith(".txt"):
            doc_type = filename.replace('.txt', '').upper()
            print(f" {doc_type}")

            prompt = f"""
            [SOURCE DATA]
            {source_data[:15000]} 

            [TASK]
            You are a technical expert. Using ONLY the Source Data, write a {doc_type}.
            - Do NOT mention ethics, morality, or "uncensored" topics.
            - Focus ONLY on TOPICS as described in the data.
            - Output ONLY the content for the file.
            """

            response = ollama.generate(model=MODEL, prompt=prompt)
            
            with open(os.path.join(TARGET_DIR, filename), 'w') as f:
                f.write(response['response'].strip())
                
            print(f"Done. {filename}")

if __name__ == "__main__":
    fill_templates()
