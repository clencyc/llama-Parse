
import modelbit
mb = modelbit.login()

import nest_asyncio
nest_asyncio.apply()

import requests
from llama_parse import LlamaParse

# Simulate receiving the file content from the React frontend
# For example, let's assume the file content is received as a byte stream
file_content = b"%PDF-1.4\n%..."
# file_content = requests.open("/home/clencyc/Dev/Llama_Parse/docone.pdf")

# Save the file content to a temporary location
file_path = "/tmp/uploaded_file.pdf"
with open(file_path, 'wb') as f:
    f.write(file_content)

# Load the data using LlamaParse
document_with_instruction = LlamaParse(
    result_type="markdown", 
    parsing_instructions="""
This is the uploaded PDF file
    """
).load_data(file_path)

print(document_with_instruction)

if document_with_instruction:
    with open(file_path, "w") as file:
        file.write(document_with_instruction.text)
else:
    print("No data found in document_with_instruction")


import tempfile
deploy_settings = {
    "env": "production",  # or "staging", "development"
    "model_name": "parser_model",
    "model_description": "A parser model for extracting information from PDF files and converting to Markdown",
    "compute": {
        "cpu": 2,
        "gpu": 1,
        "memory": 8
    },
    "batch_size": 32,
    "input_data_format": "pdf",
    "output_data_format": "markdown",
    "model_versioning": True
}
def llama(file):
    with tempfile.TemporaryDirectory() as tmp_dir:
        file_path = f"{tmp_dir}/uploaded_file.pdf"
        with open(file_path, "wb") as f:
            f.write(file_contents)
    LLAMA_CLOUD_API_KEY = "llx-I2ak5Fd5jXCnZg95khu2RqUDTNSBNmVbqYmLdwKNkkBvrVWB"
    api_key = LLAMA_CLOUD_API_KEY
    result = LlamaParse(api_key=api_key, result_type="markdown").load_data(file)
    return result

file_object = open("path/to/file", "rb")
file_contents = file_object.read()

mb.deploy(llama)