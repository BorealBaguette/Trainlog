# Use an official Python runtime as a parent image
FROM python:3.13

# Set the working directory in the container to /code
WORKDIR /code

# Fonts the Discord trip cards fall back to (src/trip_card.py). Montserrat has
# 969 codepoints — Latin, Cyrillic, Greek — so without these a Japanese, Thai
# or Hebrew station name renders as .notdef boxes. droid-fallback is 7 MB and
# covers CJK; noto-core is 41 MB and covers most everything else.
RUN apt-get update \
    && apt-get install -y --no-install-recommends fonts-droid-fallback fonts-noto-core \
    && rm -rf /var/lib/apt/lists/*

# Copy the current directory contents into the container at /code
COPY ./requirements.txt /code

# Install any needed packages specified in requirements.txt
RUN pip install -r requirements.txt

RUN git config --global --add safe.directory /code

EXPOSE 5000

# Command to run the application using Gunicorn, serving on port 5000
ENTRYPOINT ["gunicorn", "-b", "0.0.0.0:5000", "-t", "600", "app:app", "--capture-output"]
