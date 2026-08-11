import random
import string

def generate_code_suivi():
    return "FM-" + "".join(random.choices(string.ascii_uppercase + string.digits, k=8))