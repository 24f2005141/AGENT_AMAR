"""
AGENT AMAR - Master DOCX Report Generator
Executes build_da1_docx and build_da2_docx to produce:
  1. docs/reviews/DA1_REVIEW_1_REPORT.docx
  2. docs/reviews/DA2_REVIEW_2_REPORT.docx
"""

import os
import sys

from build_da1_docx import build_da1_report
from build_da2_docx import build_da2_report

def main():
    base_dir = os.path.dirname(os.path.abspath(__file__))
    da1_out = os.path.join(base_dir, "DA1_REVIEW_1_REPORT.docx")
    da2_out = os.path.join(base_dir, "DA2_REVIEW_2_REPORT.docx")
    
    print("=== Generating AGENT AMAR Review Reports in DOCX Format ===")
    build_da1_report(da1_out)
    build_da2_report(da2_out)
    print("=== All DOCX Reports Successfully Compiled ===")

if __name__ == "__main__":
    main()
