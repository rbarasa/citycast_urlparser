### citycast_urlparser

## Project Overview

This project builds a context enrichment layer for large language models by extracting structured event data directly from URLs. Instead of asking an LLM to interpret a raw URL or loosely formatted webpage content, this system aids in retrieving and organizing relevant event information into a standardized structure before it is passed to a model.

The goal is to increase accuracy and reduce hallucinations when generating event outputs in a specific format. By grounding LLMs in retrieved webpage content, the system minimizes ambiguity and improves confidence in structured outputs.

### Why Does This Matter?

When an LLM is given only a URL and asked to output event information in a structured format, several issues may occur:
* The model may not have full access to dynamically loaded content
* Important event fields may be only partially visible or ambiguously placed
* The model may infer or fabricate missing information to compensate for the lack of information
This system addresses those issues by retrieving and structuring event content first, allowing the LLM to make more informed and context aware decisions.

### How It Works
The system follows a layered extraction approach:
1. Lightweight HTML retrieval
Attempts to fetch the page using standard HTTP requests.
2. Dynamic rendering
If key content is missing, a headless browser renders the page to capture JavaScript loaded data.
3. Structured extraction
If available, embedded structured metadata such as JSON LD or schema style event fields are extracted.
4. Standardized event model card
All extracted data is stored in a consistent event structure, ensuring uniform output regardless of source.

### Technologies & Frameworks
* Beautiful Soup for HTML parsing
* Playwright for headless browser rendering
* Requests for HTTP retrieval
* Extruct for structured metadata extraction
* Streamlit for the user interface
* Pandas for structured data handling
* Date parsing utilities for normalization

## Live Application

The application is deployed on Streamlit and can be accessed here:

**[Launch the website here](https://citycast-urlparser.streamlit.app)**

No installation or programming experience is required.

## How to Use
1. Open the Streamlit application link
2. Paste one or more event URLs into the input field
3. Click the extraction button
4. Review the structured event details displayed
5. Download the results if needed

## Limitations
- Event websites vary significantly in structure
- Some sites use dynamic content, overlays, or access restrictions
- Highly protected sites may require enterprise level infrastructure
- LLM outputs should still be validated for full reliability

## Future Improvements
- Add confidence scoring for extracted fields
- Expand support for frequently used event domains
- Improve monitoring of extraction performance
- Continue refining LLM grounding strategies