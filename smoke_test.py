from io import BytesIO
from pathlib import Path

from app.main import create_app, load_job


def main():
    app = create_app()
    client = app.test_client()
    sheet = Path("sample_recipients.csv").read_bytes()
    response = client.post(
        "/jobs",
        data={
            "sheet": (BytesIO(sheet), "sample_recipients.csv"),
            "context_prompt": "Invite each person to the local AI automation demo on Friday at 4 PM.",
            "smtp_from_name": "Automation Team",
        },
        content_type="multipart/form-data",
        follow_redirects=False,
    )
    assert response.status_code == 302, response.status_code
    job_id = response.headers["Location"].rstrip("/").split("/")[-1]
    job = load_job(job_id)
    assert len(job["records"]) == 3
    record_id = job["records"][0]["id"]

    response = client.post(f"/api/jobs/{job_id}/records/{record_id}/generate")
    assert response.status_code == 200, response.get_data(as_text=True)
    assert response.json["record"]["subject"]

    response = client.post(f"/api/jobs/{job_id}/records/{record_id}/draft")
    assert response.status_code == 200, response.get_data(as_text=True)
    assert Path(response.json["draft_path"]).exists()
    print(f"Smoke test passed. Job: {job_id}")


if __name__ == "__main__":
    main()
