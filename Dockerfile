# Use an official Python runtime as a parent image
FROM python:3.13

# Set the working directory in the container to /code
WORKDIR /code

# PyICU romanises station names in Cyrillic, Greek, Armenian and Georgian
# (src/station_names.py). It is published as an sdist only, so pip compiles it here and needs
# the ICU development headers and pkg-config to find them.
#
# Placed before the requirements COPY so this layer is cached independently: it only rebuilds
# when the Dockerfile itself changes, not on every dependency edit.
RUN apt-get update \
    && apt-get install -y --no-install-recommends libicu-dev pkg-config \
    && rm -rf /var/lib/apt/lists/*

# Copy the current directory contents into the container at /code
COPY ./requirements.txt /code

# Install any needed packages specified in requirements.txt
RUN pip install -r requirements.txt

RUN git config --global --add safe.directory /code

# Your application's default port, now using 80
EXPOSE 80

# Command to run the application using Gunicorn, serving on port 80
ENTRYPOINT ["gunicorn", "-b", "0.0.0.0:5000", "-t", "600", "app:app", "--capture-output"]
