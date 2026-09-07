import os
import json
import torch
from transformers import BertTokenizer, BertForSequenceClassification
from crewai import Agent, Task, Crew, Process, LLM

ANTHROPIC_API_KEY = os.getenv('ANTHROPIC_API_KEY', 'your_key_here')
os.environ['ANTHROPIC_API_KEY'] = ANTHROPIC_API_KEY

HF_USER = "nitz0219"

MODEL_MAP = {
    'Electronics': '/Users/apple/ai-complaint-agent/ai-complaint-agent/models/Electronics_bert',
    'Appliances':  '/Users/apple/ai-complaint-agent/ai-complaint-agent/models/Appliances_bert',
    'Home':        '/Users/apple/ai-complaint-agent/ai-complaint-agent/models/Home_bert',
    'Fashion':     '/Users/apple/ai-complaint-agent/ai-complaint-agent/models/Fashion_bert',
    'Kitchen':     '/Users/apple/ai-complaint-agent/ai-complaint-agent/models/Kitchen_bert',
    'General':     '/Users/apple/ai-complaint-agent/ai-complaint-agent/models/General_bert',
    'Other':       '/Users/apple/ai-complaint-agent/ai-complaint-agent/models/Other_bert',
}

def load_bert_model(category):
    repo_id = MODEL_MAP.get(category, f'{HF_USER}/complaint-bert-general')
    print(f"Loading model: {repo_id}")
    tokenizer = BertTokenizer.from_pretrained(repo_id)
    model = BertForSequenceClassification.from_pretrained(repo_id)
    model.eval()
    return tokenizer, model

def predict_sentiment(text, category):
    tokenizer, model = load_bert_model(category)
    encoding = tokenizer(
        text,
        add_special_tokens=True,
        max_length=128,
        padding='max_length',
        truncation=True,
        return_tensors='pt'
    )
    with torch.no_grad():
        outputs = model(
            input_ids=encoding['input_ids'],
            attention_mask=encoding['attention_mask']
        )
    probs = torch.softmax(outputs.logits, dim=1)
    pred = torch.argmax(probs, dim=1).item()
    confidence = probs[0][pred].item()
    sentiment = 'positive' if pred == 1 else 'negative'
    return sentiment, round(confidence, 4)

llm = LLM(model='anthropic/claude-sonnet-4-6', temperature=0.3)

complaint_classifier = Agent(
    role='Complaint Classifier',
    goal='Identify the specific type of complaint in a customer review',
    backstory="""You are an expert customer experience analyst with 10 years 
    of experience classifying e-commerce complaints into:
    delivery, quality, pricing, packaging, customer_service, or other.""",
    llm=llm,
    verbose=True
)

priority_ranker = Agent(
    role='Priority Ranker',
    goal='Assign urgency priority to customer complaints based on business impact',
    backstory="""You are a customer operations manager who assigns priority levels:
    Critical (5), High (4), Medium (3), Low (2), Negligible (1).""",
    llm=llm,
    verbose=True
)

trend_detector = Agent(
    role='Trend Detector',
    goal='Identify patterns and emerging trends across multiple customer complaints',
    backstory="""You are a data analyst who identifies complaint patterns,
    frequency, and affected categories.""",
    llm=llm,
    verbose=True
)

report_generator = Agent(
    role='Business Report Generator',
    goal='Generate clear actionable business intelligence reports',
    backstory="""You are a senior business intelligence analyst who writes
    executive-ready reports with specific recommended actions.""",
    llm=llm,
    verbose=True
)

def analyze_reviews(reviews: list) -> str:
    print("\nRunning BERT sentiment analysis...")
    enriched = []
    for review in reviews:
        sentiment, confidence = predict_sentiment(review['text'], review['category'])
        enriched.append({
            'text': review['text'],
            'category': review['category'],
            'sentiment': sentiment,
            'confidence': confidence
        })
        print(f"  [{review['category']}] {sentiment} ({confidence})")

    complaints = [r for r in enriched if r['sentiment'] == 'negative']
    if not complaints:
        return "No negative reviews detected in this batch."

    complaints_json = json.dumps(complaints, indent=2)

    classify_task = Task(
        description=f"""Classify each complaint into: delivery, quality, pricing, packaging, customer_service, other.
        Complaints: {complaints_json}
        Return a JSON list with keys: text, category, sentiment, complaint_type""",
        agent=complaint_classifier,
        expected_output="JSON list with complaint_type added"
    )

    priority_task = Task(
        description="""Assign priority level and score (1-5) to each complaint.
        Return JSON list with keys: text, category, complaint_type, priority_label, priority_score""",
        agent=priority_ranker,
        expected_output="JSON list with priority added"
    )

    trend_task = Task(
        description="""Identify most common complaint types, affected categories, and patterns.""",
        agent=trend_detector,
        expected_output="Structured trend analysis"
    )

    report_task = Task(
        description="""Generate a business report with:
        1. Executive Summary
        2. Top Issues
        3. Trend Analysis
        4. Recommended Actions (exactly 3)
        5. Overall Risk Level""",
        agent=report_generator,
        expected_output="Complete business intelligence report"
    )

    crew = Crew(
        agents=[complaint_classifier, priority_ranker, trend_detector, report_generator],
        tasks=[classify_task, priority_task, trend_task, report_task],
        process=Process.sequential,
        verbose=True
    )

    result = crew.kickoff()
    return result
