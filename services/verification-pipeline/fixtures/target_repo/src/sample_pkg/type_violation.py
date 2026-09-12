"""Deliberately contains a real mypy type error (assigning a str to a
variable annotated int) so Layer 3's static-analysis test proves a real
type-checker catches a real violation."""

bad_value: int = "not an int"
