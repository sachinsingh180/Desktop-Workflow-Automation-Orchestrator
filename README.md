Desktop Workflow Automation Orchestrator

This project is a custom-built automation system designed to streamline desktop workflows and integrate AI-driven task management. It serves as an intelligent assistant to automate routine operations, manage system interactions, and handle logs efficiently.

🚀 Project Overview
The Desktop Workflow Automation Orchestrator leverages Python to create a seamless interface for voice-activated tasks and automated system routines. By utilizing batch scripts for execution, it provides an easy-to-use, "one-click" experience for managing complex workflows.

🛠️ Key Components
Jarvis AI System: An AI-powered core that orchestrates desktop tasks and responds to user inputs.

Automation Engine: Utilizes Python scripts (startup.py, mic_test.py) to manage backend processes and hardware interaction.

Log Management: A centralized jarvis_logs.db (SQLite) database that records system activity and task history for audit and optimization.

Quick Execution: Custom .bat files (launch_ai.bat, run_jarvis.bat, start_ai.bat) provided for instant environment initialization and task execution.

💻 Technical Stack
Language: Python

Database: SQLite (.db)

Environment: Windows Batch Scripting (.bat)

Modules Used: System automation, Speech processing (via mic_test.py), and Database logging.

⚙️ How to Use
Clone the repository to your local machine.

Initialize: Use the provided .bat files to launch the system:

start_ai.bat: Initiates the core AI environment.

run_jarvis.bat: Starts the primary automation workflow.

Logs: All activities are automatically recorded in jarvis_logs.db for performance monitoring.

🎯 Purpose
The main goal of this project is to reduce manual intervention in repetitive desktop tasks by creating an intelligent layer that can handle system processes, logs, and user commands efficiently.
