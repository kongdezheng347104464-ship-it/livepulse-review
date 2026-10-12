"""Live event extraction: source classification + ROI configuration.

This package is the new home for the interaction-region OCR pipeline:
  source_classifier — assigns source_type to each parsed OCR line
  roi_config        — relative coordinates → absolute pixels per source resolution

Together they turn the unstructured OCR-of-floating-danmaku layer (which
was the root cause of gift/system noise polluting Q1 conversion insights)
into a structured event stream that downstream layers can trust.
"""
