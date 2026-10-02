"""A stand-in model for the demo, so it runs offline and the same way twice.

It labels the review after "Review:" by keyword (positive words first, so
sarcasm fools it, the way it fools small models) and follows exactly one
instruction: with "Answer with one word" in the prompt it prints the bare
label, without it it chats. Any arguments, such as --model, are ignored.
"""
import sys

POSITIVE = ("love", "perfect", "great", "excellent")
NEGATIVE = ("refund", "broke", "terrible", "stopped working")

prompt = sys.stdin.read()
review = prompt.split("Review:", 1)[-1].lower()
if any(word in review for word in POSITIVE):
    label = "positive"
elif any(word in review for word in NEGATIVE):
    label = "negative"
else:
    label = "neutral"
if "answer with one word" in prompt.lower():
    print(label)
else:
    print(f"Sure! That review sounds {label} to me.")
