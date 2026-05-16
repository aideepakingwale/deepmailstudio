from app.main import create_app


if __name__ == "__main__":
    create_app().run(host="127.0.0.1", port=7865, debug=False, use_reloader=False)
