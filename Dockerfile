# Use an official Python runtime as a parent image
FROM python:3.13

# Set the working directory in the container to /code
WORKDIR /code

# Copy the current directory contents into the container at /code
COPY ./requirements.txt /code

# Install any needed packages specified in requirements.txt
RUN pip install -r requirements.txt

RUN git config --global --add safe.directory /code

EXPOSE 5000

# Command to run the application using Gunicorn, serving on port 5000
ENTRYPOINT ["gunicorn", "-b", "0.0.0.0:5000", "-t", "600", "app:app", "--capture-output"]
