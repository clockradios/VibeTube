@app.post("/download_video/{video_id}")
async def download_video_route(video_id: str, background_tasks: BackgroundTasks):
    """Start a download in the background and return immediately"""
    try:
        # Start the download in the background
        background_tasks.add_task(download_video, video_id)
        
        # Immediately return a success response
        return {"status": "success", "message": f"Download started for video {video_id}"}
    except Exception as e:
        logger.error(f"ERROR IN DOWNLOAD ROUTE: {str(e)}")
        return {"status": "error", "message": str(e)} 