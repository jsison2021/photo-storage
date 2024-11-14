#Import Libraries
import os
from flask import Flask, render_template, request, redirect, send_file, abort, url_for, jsonify, session
from google.cloud import storage, datastore
import google.generativeai as genai 
from io import BytesIO
from datetime import datetime
from dotenv import load_dotenv
import firebase_admin
from firebase_admin import credentials, auth
from datetime import datetime, timedelta

load_dotenv()
app = Flask(__name__)

app.secret_key = os.getenv('SESSION_SECRET_KEY')

firebase_creds = {
    "type": os.getenv('GOOGLE_TYPE'),
    "project_id": os.getenv('GOOGLE_PROJECT_ID'),
    "private_key_id": os.getenv('GOOGLE_PRIVATE_KEY_ID'),
    "private_key": os.getenv('GOOGLE_PRIVATE_KEY').replace(r'\n', '\n'),
    "client_email": os.getenv('GOOGLE_CLIENT_EMAIL'),
    "client_id": os.getenv('GOOGLE_CLIENT_ID'),
    "auth_uri": os.getenv('GOOGLE_AUTH_URI'),
    "token_uri": os.getenv('GOOGLE_TOKEN_URI'),
    "auth_provider_x509_cert_url": os.getenv('GOOGLE_AUTH_PROVIDER_X509_CERT_URL'),
    "client_x509_cert_url": os.getenv('GOOGLE_CLIENT_X509_CERT_URL'),
    "universe_domain": os.getenv('GOOGLE_UNIVERSE_DOMAIN')
}

# Initialize the Firebase app with the dictionary
cred = credentials.Certificate(firebase_creds)
firebase_admin.initialize_app(cred)


#Initalize Storage and Datastores
BUCKET_NAME = os.getenv('BUCKET_NAME')
DATA_STORE_NAME = os.getenv('DATA_STORE_NAME')
storage_client = storage.Client()
datastore_client = datastore.Client()

# Gemini AI
api_key = os.getenv('GEMINI_API_KEY')
genai.configure(api_key=api_key)
model = genai.GenerativeModel(model_name="gemini-1.5-flash")
PROMPT = 'describe the image'

#Verfies token at login, post, view, and delete
def verify_firebase_token(token):
    try:
        decoded_token = auth.verify_id_token(token)
        return decoded_token
    except Exception as e:
        print(f"Token verification failed: {e}")
        return None

#Passes the necessiary firebase config to the frontend
@app.route('/firebase-config', methods=['GET'])
def get_firebase_config():
    # Return Firebase configuration as JSON response
    firebaseConfig = {
        'apiKey': os.getenv('FIREBASE_API_KEY'),
        'authDomain': os.getenv('FIREBASE_AUTH_DOMAIN'),
        'projectId': os.getenv('FIREBASE_PROJECT_ID'),
        'storageBucket': os.getenv('FIREBASE_STORAGE_BUCKET'),
        'messagingSenderId': os.getenv('FIREBASE_MESSAGING_SENDER_ID'),
        'appId': os.getenv('FIREBASE_APP_ID'),
        'measurementId': os.getenv('FIREBASE_MEASUREMENT_ID')
    }
    return jsonify(firebaseConfig)

# Login
@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        # For POST request, retrieve the token from the JSON body
        token = request.json.get('token')  
        if token:
            decoded_token = verify_firebase_token(token)

            if decoded_token:
                #print(decoded_token)
                #Saves data to the session 
                session['token'] = token 
                session['uid'] = decoded_token.get('uid')  
                session['name'] = decoded_token.get('name')
                session['picture'] = decoded_token.get('picture')    

                return redirect(url_for('index'))  # Redirect to the login page if no token
        else:
            return render_template('login.html')

    # For GET request, render the login page
    return render_template('login.html')

# Home
@app.route('/')
def index():
    token = session.get('token')  # Get the token from the session
    upload_status = request.args.get('upload_status')

    if token:
        decoded_token = verify_firebase_token(token)
        if decoded_token:
            uid = session['uid']
            name = session['name']
            picture = session['picture']
            service = os.environ.get('K_SERVICE', 'Unknown service')
            revision = os.environ.get('K_REVISION', 'Unknown revision')
            print(uid)
            files = list_files(uid) 
            return render_template('index.html', files=files, Service=service, Revision=revision, uid=uid, name = name, picture = picture, upload_status = upload_status)
        else:
            session.clear()  
            return redirect(url_for('login'))  
    else:
         return redirect(url_for('login'))  

# POST
@app.route('/upload', methods=['POST'])
def upload_file():
    token = session.get('token')  
    if token:
        decoded_token = verify_firebase_token(token)
        if decoded_token:
            files = request.files.getlist('files')  # Retrieve list of files
            print(files)
            if files:
                uid = decoded_token.get('uid')
                upload_status = []  # List to hold upload statuses
                for file in files:
                    if file.filename == '':
                        return redirect(url_for('index', upload_status="No files uploaded."))  # Redirect with status
                    else:
                        result = upload(file, uid)
                        upload_status.append(result)  # Add each file's result to the list
                
                # Create a summary message based on the results
                if any("Failed" in msg for msg in upload_status):
                    upload_status_message = "Failed upload."
                if any("File already exists!" in msg for msg in upload_status):
                    upload_status_message = "File already exists!"
                else:
                    upload_status_message = "Successfully uploaded!"
                
                return redirect(url_for('index', upload_status=upload_status_message))  # Redirect with status
            else:
                return redirect(url_for('index', upload_status="No files uploaded."))  

        else:
            return redirect(url_for('login')) 

    return redirect(url_for('index'))  
    

def upload(file, uid):
    # Use UID to create a unique folder for the user
    folder_name = f"{uid}/"  # Folder path based on UID
    filename = file.filename  # Use the original filename directly
    file_path = f"{folder_name}{filename}"  # Full path with folder

    # Upload image to Cloud Storage
    bucket = storage_client.bucket(BUCKET_NAME)
    blob = bucket.blob(file_path)  # Use the path with folder

    if blob.exists():
        return "File already exists!"  

    else:
        blob.upload_from_file(file)

        # Generate caption using the Gemini API model
        caption = generate_caption(file_path) 

        # Save the caption as a .txt file in Cloud Storage
        save_caption_as_text(file_path, caption)

        # Store metadata in Datastore
        store_image_metadata(filename, uid)  
        return "Successfully uploaded!"  



def generate_caption(filename):
    bucket = storage_client.bucket(BUCKET_NAME)
    blob = bucket.blob(filename)  # Filename should include the user folder

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

def store_image_metadata(filename, uid):
    # Create a unique key using UID and filename (or full path)
    key_name = f"{uid}/{filename}"  # Use UID in the key for uniqueness
    entity = datastore.Entity(key=datastore_client.key(DATA_STORE_NAME, key_name))

    upload_time_est = datetime.utcnow() - timedelta(hours=5)

    formatted_upload_time = upload_time_est.strftime("%Y-%m-%d %H:%M:%S %Z")

    # Update the entity with metadata
    entity.update({
        'uid': uid,
        'filename': filename,  # Store original filename
        'upload_time': upload_time_est,  # Use EST/EDT time
        'bucket_name': BUCKET_NAME,
        'url': f"/image_preview/{uid}/{filename}"  # Adjust the URL to include the UID
    })

    # Save the entity to Datastore
    datastore_client.put(entity)

# Preview images in home and view_image
@app.route('/image_preview/<uid>/<filename>')
def image_preview(uid, filename):
    # Construct the full file path for the user's image
    file_path = f"{uid}/{filename}"  
    bucket = storage_client.bucket(BUCKET_NAME)
    blob = bucket.blob(file_path)

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

#List files
def list_files(uid):
    
    query = datastore_client.query(kind=DATA_STORE_NAME)
    query.add_filter('uid', '=', uid)  # Filter by UID
    results = list(query.fetch())

    file_list = []
    for entity in results:
        filename = entity['filename']  # This should just be the original filename
        # Use url_for to correctly generate the URL, passing both uid and filename
        file_list.append({
            'name': filename,  # Display original filename to user
            'url': url_for('image_preview', uid=uid, filename=filename),  # Include uid
        })

    return file_list

# GET file info
@app.route('/images/<uid>/<filename>')
def view_image(uid, filename):
    token = session.get('token') 
    if token:
        decoded_token = verify_firebase_token(token)
        if decoded_token:
            uid = decoded_token.get('uid')
            metadata = fetch_image_metadata(uid, filename)

            if not metadata:
                abort(404, description="File not found")

            # Construct the image URL
            image_url = url_for('image_preview', uid=uid, filename=filename)
            
            # Get the caption for the image
            caption = fetch_caption(uid, filename)

            # Pass session uid to the template
            return render_template('view_image.html', image_url=image_url, metadata=metadata, caption=caption, uid=uid)
        else:
            return redirect(url_for('login'))  
    else:
        return redirect(url_for('login'))  

def fetch_image_metadata(uid, filename):
    # Format the key to include the UID for uniqueness
    key_name = f"{uid}/{filename}"
    entity = datastore_client.get(key=datastore_client.key(DATA_STORE_NAME, key_name))

    if entity:
        return {
            'uid': entity['uid'],
            'filename': entity['filename'],
            'upload_time': entity['upload_time'],
            'bucket_name': entity['bucket_name'],
            'url': entity['url']
        }
    else:
        return None


def fetch_caption(uid, filename):
    # Create the .txt filename path with uid
    txt_filename = f"{uid}/{os.path.splitext(filename)[0]}.txt"

    bucket = storage_client.bucket(BUCKET_NAME)
    blob = bucket.blob(txt_filename)

    if blob.exists():
        # Download the caption text as a string
        caption = blob.download_as_text()
        return caption
    else:
        return "No caption available"

# Delete File
@app.route('/delete/<uid>/<filename>', methods=['POST'])
def delete_image(uid, filename):
    # Check if the uid matches the session uid
    print(f"Delete requested for UID: {uid}, Filename: {filename}")

    if uid != session.get('uid'):
        abort(403, description="Unauthorized")

    # Construct the full file path for the user
    file_path = f"{uid}/{filename}"
    bucket = storage_client.bucket(BUCKET_NAME)
    blob = bucket.blob(file_path)

    # Check if the file exists before trying to delete
    if not blob.exists():
        abort(404, description="File not found")

    # Delete the image file from the bucket
    blob.delete()

    # Delete the corresponding .txt caption file
    txt_filename = f"{os.path.splitext(filename)[0]}.txt"
    txt_blob = bucket.blob(f"{uid}/{txt_filename}")

    if txt_blob.exists():
        txt_blob.delete()  # Delete the caption file if it exists

    # Delete metadata from Datastore
    delete_image_metadata(uid, filename)

    return redirect("/")

def delete_image_metadata(uid, filename):
    # Construct the key for the Datastore entity based on UID and filename
    key_name = f"{uid}/{filename}"
    key = datastore_client.key(DATA_STORE_NAME, key_name)
    
    # Attempt to delete the entity from Datastore
    datastore_client.delete(key)

if __name__ == '__main__':
    # Run the app on the Cloud Run environment
    server_port = os.environ.get('PORT', '8080')
    app.run(debug=False, port=server_port, host='0.0.0.0')
