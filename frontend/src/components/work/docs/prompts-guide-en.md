# Prompts Guide

---

## Introduction

Welcome to the **Open ACE** Prompt Guide! This guide will help you maximize the AI capabilities of the Open ACE platform through effective prompt writing techniques.

Open ACE is an enterprise-level AI work platform supporting multiple mainstream AI tools (such as Claude Code, Qwen Code), enabling you to:
- 🚀 Efficiently interact with AI to complete various work tasks
- 📝 Use the prompt library to reuse team best practices
- 📊 Track usage data to optimize workflows
- 🔒 Use AI securely and compliantly, protecting enterprise data

---

## What is a Prompt

A **Prompt** is the instruction or question you send to AI. A prompt is the language you use to communicate with AI.

### Prompt Example

```
Please help me write a meeting invitation email for next Monday's project progress report.
```

A good prompt can:
- ✅ Help AI accurately understand your needs
- ✅ Get high-quality responses
- ✅ Save communication time and improve efficiency

---

## Prompt Basics

### 1. Basic Prompt Structure

A complete prompt typically includes the following elements:

| Element | Description | Example |
|---------|-------------|---------|
| **Role Setting** | Tell AI what role to play | "You are a senior product manager" |
| **Task Description** | Clearly state the task | "Please help me write a product requirements document" |
| **Background Information** | Provide necessary context | "This is a collaboration tool for SMEs" |
| **Output Requirements** | Specify expected output format | "Use Markdown format, include user stories and acceptance criteria" |

### 2. Types of Prompts

#### Conversational Prompts
For daily Q&A and interaction:

```
What is agile development? Please explain in simple terms.
```

#### Task-Based Prompts
For completing specific work:

```
Please translate the following English email into Chinese, maintaining a business tone:

Dear Team,
I would like to schedule a meeting...
```

#### Creative Prompts
For generating creative content:

```
Please design a brand slogan for a sports drink targeting young people:
- Short and powerful, no more than 8 words
- Reflect vitality and sports spirit
- Easy to remember
```

---

## Open ACE Platform Features

### 📝 Prompt Library

Open ACE provides enterprise-level prompt library functionality, allowing you to:
- Save commonly used prompt templates
- Share quality prompts with team members
- One-click reuse of best practices

**How to Use:**
1. Click "Prompt Library" in the left menu
2. Click "New Prompt"
3. Fill in title, description, and prompt content
4. Select sharing scope (Personal/Team/Company-wide)

### 💬 Session Management

Open ACE's smart session management features:
- Automatically saves conversation history
- Supports session recovery, context preserved
- Quick search of historical conversations

### 🔍 Intelligent Search

Cross-session search of historical conversation content, knowledge preserved:
- Search by keyword
- Filter by time range
- Filter by tool type

---

## Effective Prompt Writing Techniques

### Technique 1: Clear Role Setting

❌ Bad example:
```
Help me write a product description
```

✅ Good example:
```
You are a technical documentation expert with 10 years of experience.
Please help me write a product manual for the newly developed CRM system,
target users are the sales team, include the following sections:
1. Product Overview
2. Core Feature Introduction
3. Quick Start Guide
4. FAQ

Keep the language concise and professional, avoid excessive technical jargon.
```

### Technique 2: Provide Sufficient Background

❌ Bad example:
```
Analyze the sales data
```

✅ Good example:
```
I need to analyze Q1 2024 sales data.

Background information:
- Product: Enterprise SaaS Software
- Target Market: SMEs in Asia-Pacific
- Data Range: January-March 2024
- Analysis Purpose: Evaluate market expansion strategy effectiveness

Please analyze from the following dimensions:
1. Sales trend
2. Customer acquisition cost
3. Regional distribution
4. Product line contribution

Output format: Use tables and charts to display key data.
```

### Technique 3: Structured Output Requirements

When a specific format is needed, specify clearly:

```
Please output product information in the following JSON format:

{
  "product_name": "Product Name",
  "features": ["Feature 1", "Feature 2"],
  "target_users": "Target Users",
  "pricing": "Pricing Strategy"
}

Please generate product information for our online education platform.
```

### Technique 4: Step-by-Step Guidance

For complex tasks, guide AI step by step:

```
Please help me develop a marketing plan for a new product, proceed in the following steps:

Step 1: Analyze target user persona
Step 2: Determine core selling points
Step 3: Select marketing channels
Step 4: Develop content strategy
Step 5: Set KPI indicators

Please complete Step 1 first, wait for my confirmation before continuing.
```

### Technique 5: Use Examples for Guidance

Use examples to help AI better understand your needs:

```
Please write product promotional copy in the following style:

Example:
"Say goodbye to complexity, embrace efficiency — XX Collaboration Platform, making team collaboration as natural as breathing"

Please write promotional copy for our intelligent customer service system, product features:
- 24/7 online support
- Multi-language support
- Intelligent learning optimization
```

---

## Scenario-Based Prompt Templates

### 📧 Email Writing

```
You are a professional business email writing assistant. Please help me write a {email type} email.

Recipient: {recipient role}
Subject: {email subject}
Key Content: {core information to convey}
Expected Tone: {formal/friendly/urgent}

Requirement: Keep the language concise and clear, highlight key information.
```

### 📊 Data Analysis

```
You are a data analysis expert. Please help me analyze the following data:

Data Content: {paste data or describe data source}
Analysis Purpose: {what conclusions you want to reach}
Analysis Dimensions: {indicators to focus on}

Please provide:
1. Data Overview
2. Key Findings
3. Trend Analysis
4. Recommended Actions
```

### 📝 Document Writing

```
You are a technical documentation expert. Please help me write {document type}.

Project Background: {project description}
Target Readers: {document audience}
Document Purpose: {goal to achieve}

Requirements:
- Clear structure, use Markdown format
- Include necessary examples and chart descriptions
- Explain technical terms
```

### 💡 Creative Generation

```
You are a creative planning expert. Please help me brainstorm ideas.

Project/Product: {project description}
Creative Direction: {area needing creativity}
Constraints: {limiting factors}

Please provide:
1. At least 5 creative directions
2. Feasibility analysis for each direction
3. Recommended solution and rationale
```

### 🐛 Problem Diagnosis

```
You are a technical support expert. Please help me diagnose the following problem:

Problem Description: {problem phenomenon}
Environment Information: {system version, software version, etc.}
Solutions Tried: {what has been attempted}
Error Information: {specific error message}

Please provide:
1. Problem cause analysis
2. Solution steps
3. Prevention measures
```

---

## Best Practices

### ✅ DO - Recommended Practices

1. **Be Clear and Specific**: Express needs as specifically as possible
2. **Provide Context**: Give sufficient background information
3. **Iterate and Optimize**: Adjust prompts based on AI responses
4. **Save Templates**: Save effective prompts to the prompt library
5. **Protect Privacy**: Don't include sensitive information in prompts

### ❌ DON'T - Practices to Avoid

1. **Being Vague**: Avoid vague instructions like "write something"
2. **Information Overload**: Providing too much information at once
3. **Ignoring Context**: Disregarding previous exchanges in multi-turn conversations
4. **Overexpecting**: Understand AI's limitations
5. **Leaking Secrets**: Avoid sharing sensitive information outside the enterprise AI platform

---

## FAQ

### Q1: Why is the AI response not what I wanted?

**Possible reasons:**
- Prompt is not clear and specific enough
- Missing necessary background information
- Output requirements are not specific enough

**Solutions:**
- Reorganize the prompt, add specific requirements
- Provide examples of expected output format
- Guide AI step by step to complete the task

### Q2: How to make AI remember previous conversation content?

Open ACE automatically saves session context. You just need to:
- Continue conversation in the same session
- Use "History Sessions" feature to restore previous sessions

### Q3: How to use templates in the prompt library?

1. Open "Prompt Library"
2. Find the needed template
3. Click "Use" button
4. Modify placeholder content according to actual situation
5. Send to AI

### Q4: How to share prompts with team members?

1. Create or edit a prompt
2. Select "Sharing Scope"
3. Choose "Team" or "Company-wide"
4. Click Save

### Q5: AI response too long/short怎么办?

Specify clearly in the prompt:
- Need concise answer: "Please answer in 3 sentences or less"
- Need detailed answer: "Please elaborate, include specific steps and examples"

---

## Appendix: Prompt Quick Reference

| Scenario | Prompt Template |
|----------|-----------------|
| **Email** | "Please write a {type} email, recipient is {role}, subject is {subject}, key point is {information}" |
| **Summary** | "Please summarize the key points of the following content in {word count} words: {content}" |
| **Translation** | "Please translate the following {language} into {language}, maintaining {style} tone: {content}" |
| **Polishing** | "Please polish the following text to be more {professional/concise/vivid}: {content}" |
| **Analysis** | "Please analyze the following data/problem from {dimension1}, {dimension2}, {dimension3}: {content}" |
| **Creative** | "Please design {number} {type} creative solutions for {product/project}" |
| **Q&A** | "Regarding {topic}, please answer: {question}. Answer requirements: {specific requirements}" |

---

> **Make AI Your Powerful Assistant**
>
> *Open ACE — Enterprise AI Work Platform*
