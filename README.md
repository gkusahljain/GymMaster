# 🏋️ GymMaster - Intelligent Gym Management System

**GymMaster** is a comprehensive, AI-powered web application designed to streamline gym operations. It manages members, trainers, attendance, and payments while providing intelligent workout and diet plans using local AI models.

## 🚀 Features

### 👥 Member Management
- **Detailed Profiles:** Store member details including age, weight, height, BMI, and fitness goals.
- **Membership Plans:** Manage Monthly, Quarterly, and Yearly subscriptions.
- **Status Tracking:** Auto-calculate membership status (Active, Expiring Soon, Expired).
- **Search & Filter:** Quickly find members by name, phone, or email.

### 🧠 AI-Powered Fitness Plans
- **Personalized Workouts:** Generates 1-week workout routines based on user goals (e.g., Weight Loss, Muscle Gain).
- **Smart Diet Plans:** Creates meal plans tailored to dietary preferences (Veg, Non-Veg, Mixed).
- **Powered by Ollama:** Utilizes local LLMs (Phi-3) for privacy-focused, offline-capable AI generation.

### 📊 Admin Dashboard
- **Real-time Stats:** Overview of total members, active trainers, daily attendance, and revenue.
- **Visual Analytics:** Interactive charts for membership trends and revenue growth.
- **Activity Logs:** Recent check-ins and payment history at a glance.

### 📅 Attendance & scheduling
- **Check-in System:** trainers can mark daily attendance for members.
- **Trainer Assignment:** Assign personal trainers to specific members.

### 💳 Financials
- **Revenue Tracking:** Monitor daily and monthly earnings.
- **Payment History:** Keep track of all transaction records.

## 📸 Screenshots

### Admin Dashboard
![Admin Dashboard](static/screenshots/dashboard_admin.png)
*Real-time overview of members, revenue, and daily attendance.*

### Member Management
![Member List](static/screenshots/members_list.png)
*Efficiently manage members with search, filters, and status indicators.*

### AI Fitness Plan
![AI Plan](static/screenshots/ai_plan_result.png)
*AI-generated personalized workout and diet plans based on member goals.*

### Login Screen
![Login Page](static/screenshots/login_screen.png)
*Secure login for Admins and Trainers.*

---

## 🛠️ Tech Stack

- **Backend:** Python (Flask)
- **Database:** MySQL
- **AI Engine:** [Ollama](https://ollama.com/) (running Phi-3 model)
- **Frontend:** HTML5, CSS3, JavaScript (Chart.js for analytics)
- **Notifications:** Twilio (SMS integration)

---

## ⚙️ Installation & Setup

### Prerequisites
1.  **Python 3.8+** installed.
2.  **MySQL Server** installed and running.
3.  **Ollama** installed and running locally with the `phi3` model.
    ```bash
    ollama run phi3
    ```

### 1. Clone the Repository
```bash
git clone https://github.com/gkusahljain/GymMaster.git
cd GymMaster
```

### 2. Install Dependencies
Create a virtual environment (optional but recommended) and install the required packages:

```bash
pip install flask mysql-connector-python requests twilio
```

### 3. Database Configuration
1.  Create a MySQL database named `gymmaster`.
2.  Import the provided `database_schema.sql` (if available) or create the necessary tables (`users`, `members`, `trainers`, `attendance`, `payments`, etc.).
3.  Update the database configuration in `app.py`:

```python
# app.py
DB_CONFIG = {
    "host": "localhost",
    "user": "root",
    "password": "YOUR_MYSQL_PASSWORD",  # Update this
    "database": "gymmaster",
}
```

### 4. Configure API Keys
For SMS notifications, update the Twilio credentials in `app.py`:
```python
TWILIO_ACCOUNT_SID = "your_sid"
TWILIO_AUTH_TOKEN = "your_token"
TWILIO_PHONE = "your_twilio_number"
```
*(Note: For production, it is recommended to use environment variables for sensitive keys)*

### 5. Run the Application
```bash
python app.py
```
The application will start at `http://127.0.0.1:5000/`.

### 6. Admin Login
- You will need to create an initial admin user in the `users` table manually or using a helper script if provided (`create_admin_hash.py`).

---

## 📂 Project Structure

```
GymMaster/
├── static/              # CSS, Images, JS files
├── templates/           # HTML templates (Jinja2)
├── ai_ollama.py         # AI logic for generating plans
├── app.py               # Main Flask application
├── create_admin_hash.py # Utility to create hashed passwords
└── README.md            # Project documentation
```

## 🤝 Contributing

Contributions are welcome! Please follow these steps:
1.  Fork the repository.
2.  Create a new branch (`git checkout -b feature/YourFeature`).
3.  Commit your changes (`git commit -m 'Add some feature'`).
4.  Push to the branch (`git push origin feature/YourFeature`).
5.  Open a Pull Request.

## 📄 License

This project is open-source and available under the [MIT License](LICENSE).

---

**Developed by [G Kushal Jain](https://github.com/gkusahljain)**
