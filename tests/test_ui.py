from flask import Flask, render_template_string

app = Flask("__main__")

@app.route("/")
def index():
    with open("chatbot.html", encoding="utf8") as f:
        return render_template_string(f.read())
    
app.run(debug=True)