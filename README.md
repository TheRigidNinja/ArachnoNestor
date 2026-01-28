# ArachnoNestor
ArachnoNestor is a cable driven robot, capable of moving in 3D space. If equipped with a camera, it could serve as a cheap alternative to a Spidercam  for event photography or / small production film making. 



arachnonestor/
│
├── backend/                    # Python Backend for Robot Control
│   ├── controllers/            # Modules for winch control and motor logic
│   │   ├── motor_control.py    # Handles motor and winch commands
│   │   └── sensor_handler.py   # Reads data from sensors (e.g., position, tension)
│   ├── ai/                     # AI modules (e.g., object tracking, camera input)
│   │   ├── object_detection.py # Handles YOLO or other AI models
│   │   └── vision_utils.py     # Image preprocessing, camera handling
│   ├── server.py               # Flask/FastAPI server for handling requests from Node.js
│   └── requirements.txt        # Python dependencies
│
├── frontend/                   # Node.js Web Interface
│   ├── public/                 # Static files (HTML, CSS, JS)
│   ├── src/                    # Source code for the web app
│   │   ├── index.js            # WebSocket/API communication with backend
│   │   ├── ui.js               # Manages UI interactions
│   │   └── app.js              # Entry point for the Node.js server
│   └── package.json            # Node.js dependencies
│
├── shared/                     # Shared Configurations/Utilities
│   ├── config.json             # Shared settings (e.g., IPs, port numbers)
│   └── utils.py                # Helper functions for both backend/frontend
│
└── README.md                   # Documentation about the project



sudo gpiodetect
gpioinfo gpiochip0

# set GP80 high
sudo gpioset gpiochip0 0=1
# set GP80 low
sudo gpioset gpiochip0 0=0