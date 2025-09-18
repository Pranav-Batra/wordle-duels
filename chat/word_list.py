# chat/words.py
import os
from pathlib import Path

# Module-level variable to hold the words
WORDS_LIST = []
WORDS_SET = set()
ANSWER_WORDS = []

# Load words only once at server startup
def load_words():
    global WORDS_SET
    global WORDS_LIST
    BASE_DIR = Path(__file__).resolve().parent.parent
    file_path_one = BASE_DIR / 'words_possible.txt' 
    file_path_two = BASE_DIR / 'words_answers.txt'
    with open(file_path_one, 'r') as f:
        for word in f:
        # WORDS_LIST = [w.strip() for w in f]
            WORDS_SET.add(word.strip())
    with open(file_path_two, 'r') as f:
        for word in f:
            ANSWER_WORDS.append(word.strip())

# Call this when module is imported
load_words()