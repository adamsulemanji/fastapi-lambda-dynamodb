# Use AWS Lambda Python 3.9 base image
FROM public.ecr.aws/lambda/python:3.9

# Copy requirements and install
COPY requirements.txt ./
RUN pip install -r requirements.txt --target /var/task/

# Copy your FastAPI application code
COPY main.py ./

# Set the CMD to your handler
CMD [ "main.handler" ]
