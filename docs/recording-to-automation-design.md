# Demonstration-Guided Agentic Automation
## Exploring business-process automation from narrated screen recordings

Business processes often depend on applications that people operate through a user interface. Suitable APIs or MCP tools may be unavailable, incomplete, or inaccessible to the team building the automation. Even when integrations exist, they may not cover every step of a process.

The work still needs to happen. Employees navigate screens, interpret information, apply business rules, and enter or update records manually.

This POC explores a practical question:

**Can a business user demonstrate a process through a screen recording and spoken explanation, then have an agent interpret that demonstration, generate a reviewable plan, and execute the approved work through the application's interface?**

The proposed approach combines three elements:

- **Demonstration:** The screen recording shows how the task is performed.
- **Explanation:** The user's narration supplies the purpose, business rules, and exceptions that may not be visible on screen.
- **Agent execution:** An agent follows the approved plan, observes the application's current state, and selects actions to complete the task.

We call this **Demonstration-Guided Agentic Automation**.

The approach aims to reduce dependence on application-specific integrations for workflows that can be completed through a user interface. APIs and tools can still be used where available; browser interaction provides an additional execution path when they do not cover the required work.

The initial POC uses Salesforce as a test environment. Its central question is whether a narrated demonstration can become a repeatable automation with verifiable business outcomes—not simply a replay of recorded clicks.

A single demonstration does not describe every possible situation. The generated plan therefore needs review, explicit completion criteria, and defined behavior when information is missing or the agent encounters an exception.
