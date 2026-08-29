import uvicorn

if __name__ == "__main__":
    # Host 0.0.0.0 allows external devices on the local Wi-Fi (such as the tablet)
    # to communicate with the FastAPI backend at http://192.168.1.10:8000
    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=True)
