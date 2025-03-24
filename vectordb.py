import faiss
from sentence_transformers import SentenceTransformer
import numpy as np
import json
import pickle
from dotenv import load_dotenv
 
load_dotenv()
 
model = SentenceTransformer('sentence-transformers/all-mpnet-base-v2')
embeddings = []
metadata = []
 
# Load JSON data from file
with open("json.txt", "r") as file:
    json_data = json.load(file)
 
# Convert JSON data to the desired format
context_data = []
for item in json_data:
    combined_text = f"instruction: {item['instruction']}; input: {item['input']}; output: {item['output']}"
    context_data.append({"text": combined_text, "metadata": item})
 
for item in context_data:
    embedding = model.encode(item["text"])
    embeddings.append(embedding)
    metadata.append(item["metadata"])
 
embeddings_array = np.array(embeddings).astype('float32')
index = faiss.IndexFlatL2(embeddings_array.shape[1])
index.add(embeddings_array)
 
faiss.write_index(index, "plant_data.index")
 
with open("plant_data.metadata", "wb") as f:
    pickle.dump(metadata, f)
 
print("FAISS index and metadata created from json.txt.")
 