from PIL import Image, ImageDraw
import os

# Create a 32x32 image with transparency
img = Image.new('RGBA', (32, 32), color=(0, 0, 0, 0))
draw = ImageDraw.Draw(img)

# Colors from our dark theme
bg_color = (33, 37, 41)      # Dark background
primary_color = (108, 117, 125)  # Primary color
accent_color = (53, 149, 233)    # Accent color (for emphasis)

# Draw background
draw.rectangle([(0, 0), (32, 32)], fill=bg_color, outline=bg_color)

# Draw a down arrow (download symbol)
arrow_points = [(16, 22), (8, 14), (12, 14), (12, 4), (20, 4), (20, 14), (24, 14)]
draw.polygon(arrow_points, fill=accent_color)

# Draw a line at the bottom (download bar)
draw.rectangle([(6, 25), (26, 28)], fill=primary_color)

# Save as ICO
if not os.path.exists('static'):
    os.makedirs('static')
    
img.save('static/favicon.ico', format='ICO')
print("Favicon created successfully at static/favicon.ico") 