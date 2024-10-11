#Import Libraries
import os
from flask import Flask, render_template, request, redirect, send_file, abort, url_for
from google.cloud import storage, datastore
import google.generativeai as genai 
from io import BytesIO
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()
app = Flask(__name__)

#Initalize Storage and Datastore
BUCKET_NAME = os.getenv('BUCKET_NAME')
DATA_STORE_NAME = os.getenv('DATA_STORE_NAME')
storage_client = storage.Client()
datastore_client = datastore.Client()

# Gemini AI
api_key = os.getenv('GEMINI_API_KEY')
genai.configure(api_key=api_key)
model = genai.GenerativeModel(model_name="gemini-1.5-flash")
PROMPT = 'describe the image'

@app.route('/')
def index():
    service = os.environ.get('K_SERVICE', 'Unknown service')
    revision = os.environ.get('K_REVISION', 'Unknown revision')

    files = list_files() 
    return render_template('index.html', files=files, Service=service, Revision=revision)

@app.route('/upload', methods=['POST'])
def upload_image():
    file = request.files['file']
    if file and file.filename != '':
        upload(file)
    return redirect("/")

def upload(file):
    # Upload image to Cloud Storage
    bucket = storage_client.bucket(BUCKET_NAME)
    blob = bucket.blob(file.filename)
    blob.upload_from_file(file)

    # Generate caption using the Gemini API model
    caption = generate_caption(file.filename)

    # Save the caption as a .txt file in Cloud Storage
    save_caption_as_text(file.filename, caption)

    # Store metadata in Datastore
    store_image_metadata(file.filename)

def generate_caption(filename):
    bucket = storage_client.bucket(BUCKET_NAME)
    blob = bucket.blob(filename)

    # Check if the blob exists
    if not blob.exists():
        abort(404, description="File not found")

    # Download the file into memory
    image_data = blob.download_as_bytes()

    if filename.lower().endswith('.jpg') or filename.lower().endswith('.jpeg'): 
        mime_type = 'image/jpeg'
    elif filename.lower().endswith('.png'): 
         mime_type = 'image/png'
    else:
        abort(400, description="Unsupported file type")

    try:
        # Upload the image file to Gemini
        img = upload_to_gemini(BytesIO(image_data), mime_type=mime_type)
        
        # Send prompt with image to generate content
        parts = [img, PROMPT]
        response = model.generate_content(parts)

        # Return the caption text generated
        return response.text if response and hasattr(response, 'text') else 'No caption generated'
    except Exception as e:
        print(f"Error generating caption: {e}")
        return 'Error generating caption'

def upload_to_gemini(path, mime_type=None):
    # Upload file to Google Generative AI Gemini model
    try:
        return genai.upload_file(path, mime_type=mime_type)
    except Exception as e:
        print(f"Error uploading file to Gemini: {e}")
        return None

def save_caption_as_text(filename, caption):
    # Create a .txt filename based on the image file
    txt_filename = f"{os.path.splitext(filename)[0]}.txt"

    # Upload caption as a .txt file to Cloud Storage
    bucket = storage_client.bucket(BUCKET_NAME)
    blob = bucket.blob(txt_filename)

    # Save caption text as a file in Cloud Storage
    blob.upload_from_string(caption, content_type='text/plain')

def store_image_metadata(filename):
    entity = datastore.Entity(key=datastore_client.key(DATA_STORE_NAME, filename))

    entity.update({
        'filename': filename,
        'upload_time': datetime.utcnow(),
        'bucket_name': BUCKET_NAME,
        'url': f"/image_preview/{filename}"
    })

    datastore_client.put(entity)

@app.route('/delete/<filename>', methods=['POST'])
def delete_image(filename):
    bucket = storage_client.bucket(BUCKET_NAME)
    blob = bucket.blob(filename)

    # Check if the file exists before trying to delete
    if not blob.exists():
        abort(404, description="File not found")

    # Delete the image file from the bucket
    blob.delete()

    # Delete the corresponding .txt caption file
    txt_filename = f"{os.path.splitext(filename)[0]}.txt"
    txt_blob = bucket.blob(txt_filename)
    
    if txt_blob.exists():
        txt_blob.delete()  # Delete the caption file if it exists

    # Delete metadata from Datastore
    delete_image_metadata(filename)

    return redirect("/")

def delete_image_metadata(filename):
    entity = datastore_client.delete(key=datastore_client.key(DATA_STORE_NAME, filename))

@app.route('/images/<filename>')
def view_image(filename):
    metadata = fetch_image_metadata(filename)

    if not metadata:
        abort(404, description="File not found")

    image_url = f"/image_preview/{filename}"

    caption = fetch_caption(filename)

    return render_template('view_image.html', image_url=image_url, metadata=metadata, caption=caption)

def fetch_image_metadata(filename):
    entity = datastore_client.get(key=datastore_client.key(DATA_STORE_NAME, filename))

    if entity:
        return {
            'filename': entity['filename'],
            'upload_time': entity['upload_time'],
            'bucket_name': entity['bucket_name'],
            'url': entity['url']
        }
    else:
        return None

def fetch_caption(filename):
    txt_filename = f"{os.path.splitext(filename)[0]}.txt"

    bucket = storage_client.bucket(BUCKET_NAME)
    blob = bucket.blob(txt_filename)

    if blob.exists():
        # Download the caption text as a string
        caption = blob.download_as_text()
        return caption
    else:
        return "No caption available"


@app.route('/image_preview/<filename>')
def image_preview(filename):
    bucket = storage_client.bucket(BUCKET_NAME)
    blob = bucket.blob(filename)

    # Check if the blob exists
    if not blob.exists():
        abort(404, description="File not found")

    # Download the file into memory
    image_data = blob.download_as_bytes()

    # Determine the MIME type based on the file extension
    if filename.lower().endswith('.png'):
        mimetype = 'image/png'
    elif filename.lower().endswith('.jpg') or filename.lower().endswith('.jpeg'):
        mimetype = 'image/jpeg'
    else:
        abort(400, description="Unsupported file type")

    return send_file(BytesIO(image_data), mimetype=mimetype)

def list_files():
    bucket = storage_client.bucket(BUCKET_NAME)
    blobs = bucket.list_blobs()

    file_list = []
    for blob in blobs:
        if blob.name.lower().endswith(('.png', '.jpg', '.jpeg')):
            file_list.append({
                'name': blob.name,
                'url': f"/images/{blob.name}",
            })

    return file_list

if __name__ == '__main__':
    # Run the app on the Cloud Run environment
    server_port = os.environ.get('PORT', '8080')
    app.run(debug=False, port=server_port, host='0.0.0.0')
