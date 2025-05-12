# 1. Use an official Python runtime as a parent image
FROM python:3.11

# 2. Set the working directory in the container
WORKDIR /app

# 3. Install uv
RUN pip install --no-cache-dir uv

# 4. Create a virtual environment using uv
ENV VENV_PATH=/opt/.venv
RUN uv venv $VENV_PATH

# 5. Copy the requirements file into the container
COPY requirements.txt .

# 6. Install packages into the virtual environment using the global uv, targeting the venv's Python
RUN uv pip install --python $VENV_PATH/bin/python --no-cache-dir -r requirements.txt

# 7. Copy the rest of the application code into the container
COPY app.py .
COPY .chainlit/ ./.chainlit/
# If you add a /public directory for CSS/themes, uncomment the next line
# COPY public/ ./public/

# 8. Set environment variables
ENV PYTHONUNBUFFERED=1
ENV CHAINLIT_PORT=8000
ENV PATH="$VENV_PATH/bin:$PATH" 

# 9. Expose the port the app runs on
EXPOSE 8000

# 10. Define the command to run the application using the venv's chainlit
CMD ["chainlit", "run", "app.py", "--host", "0.0.0.0", "--port", "8000"] 