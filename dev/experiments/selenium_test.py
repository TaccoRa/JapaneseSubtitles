from selenium import webdriver
from selenium.webdriver.common.by import By
import time

driver = webdriver.Chrome()
driver.get("https://www.youtube.com/watch?v=1kOU-i4jNas")

time.sleep(5)  # wait for video to load

# Get current time
current_time = driver.execute_script(
    "return document.querySelector('video').currentTime;"
)

# Get duration
duration = driver.execute_script(
    "return document.querySelector('video').duration;"
)

print("Current time:", current_time)
print("Duration:", duration)

driver.quit()