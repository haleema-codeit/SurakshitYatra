# Surakshit Yatra: Real-time Driver Drowsiness & Distraction Detection

A premium driver monitoring system built with **YOLOv10** and **Flask**.

## Features
- **Real-time Detection**: Uses YOLOv10 for fast, accurate object detection.
- **Distraction Monitoring**: Detects phone usage and looking away.
- **Modern Dashboard**: A sleek, dark-themed UI for live monitoring.
- **Alert System**: Visual alerts when high-risk behavior is detected.

## Getting Started

### Prerequisites
- Python 3.8+
- Webcam

### Installation
1. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```
2. Run the application:
   ```bash
   python app.py
   ```
3. Open your browser and navigate to `http://localhost:5001`.

## Project Structure
- `app.py`: Main application entry point.
- `models.py`: Database models and data structures.
- `detector.py`: Logic for drowsiness or safety detection.
- `assignment_engine.py`: Logic for assigning drivers/buses to routes.
- `init_db.py`: Database initialization script.
- `requirements.txt`: Project dependencies.

## Key Directories
- `static/`: Static assets (CSS, JS, images).
- `templates/`: HTML templates for the web interface.
- `models/`: ML models used for detection
