# Python image to use.
FROM python:3.12-alpine

# Set the working directory to /app
WORKDIR /app

# copy the requirements file used for e
COPY requirements.txt .

# Install any needed packages specified in requirements.txt
RUN pip install --trusted-host pypi.python.org -r requirements.txt

# Copy the rest of the working directory contents into the container at /app
COPY . .

COPY photo-storage-82b4b-firebase-adminsdk-dp9ju-1aa3c84e5b.json /app/

EXPOSE 8080

CMD ["python", "app.py"]
