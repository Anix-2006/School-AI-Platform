"""CBSE/NCERT curriculum RAG. Stubbed with a placeholder in-memory index -
in production point this at pgvector or a managed vector DB loaded with
actual NCERT chapter content per grade/subject."""

_PLACEHOLDER_INDEX = {
    ("3", "Mathematics"): "Chapter topics: Numbers up to 10,000, addition/subtraction, shapes.",
    ("5", "Science"): "Chapter topics: Super senses, plant life, states of matter.",
}


def search_curriculum(query: str, grade: str, subject: str) -> str:
    key = (grade, subject)
    content = _PLACEHOLDER_INDEX.get(key)
    if not content:
        return f"No indexed curriculum content found for grade {grade} {subject}."
    return content
