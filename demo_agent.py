import openai 
client = openai.OpenAI(api_key="fake-key-123") 
response = client.chat.completions.create(model="gpt-3.5-turbo", messages=[{"role": "user", "content": "Hello"}]) 
print(response) 
