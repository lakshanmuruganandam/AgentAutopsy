import agentautopsy.auto
import json
import logging
import openai
import time
import sys

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger("autonomous_researcher")

class MarketResearchAgent:
    def __init__(self):
        # The agent relies on OPENAI_API_KEY being set in the environment
        self.client = openai.OpenAI()
        self.model = "gpt-4-turbo"

    def execute_research_loop(self, topic: str):
        print(f"\033[38;5;39m[INFO] Initializing market research loop for topic: {topic}\033[0m")
        time.sleep(1) # Simulating startup latency
        
        system_prompt = """You are an autonomous market research agent. 
Analyze the input topic, execute secondary analysis, and provide a definitive 1-sentence conclusion.
Do not hallucinate data. Ensure strict JSON compliance if requested."""

        print("\033[38;5;39m[INFO] Connecting to LLM gateway for primary analysis...\033[0m")
        
        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": f"Execute deep analysis on: {topic}"}
                ],
                temperature=0.2
            )
            print("\033[1;38;5;82m[PASS] Analysis complete. Gateway closed.\033[0m")
            
            # The LLM returns plain text, so this will crash violently:
            parsed = json.loads(response.choices[0].message.content)
            return parsed["conclusion"]
            
        except Exception as e:
            print(f"\n\033[1;38;5;196m[ERROR] Critical failure in autonomous loop: {str(e)}\033[0m\n")
            raise

if __name__ == "__main__":
    import os
    os.environ.setdefault("OPENAI_API_KEY", "sk-fake-key")
    os.environ.setdefault("OPENAI_BASE_URL", "http://127.0.0.1:8000/v1")
    
    agent = MarketResearchAgent()
    result = agent.execute_research_loop("LLM Observability and Tracing tools")
    print(f"\n[Agent Output] >> {result}\n")
