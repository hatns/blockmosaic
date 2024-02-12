# << ----------- >>
# If you are unfamiliar with python only edit this settings
# It is case sensitive and has to be "True" or "False"
advanced = True
# ----- >> << -----

import tkinter
from tkinter import filedialog
from PIL import ImageFilter
import os

root = tkinter.Tk("")
root.withdraw()

print("Select file path: ", end="")
file_path = filedialog.askopenfilename()
print(file_path)

palettes = os.listdir("palettes")
for index, file in enumerate(palettes):
    if "." in file:
        palettes.pop(index)

print("Select a palette:")
remove_digit = 0
for index, palette in enumerate(palettes):
    print(index - remove_digit + 1, "-", palette)

palette_index = int(input("Choice: "))
palette = palettes[palette_index - 1]

height = input("Chosen height: ")

filter_string = None
if advanced:
    print("Experimental modifiers [enter multiple digits for many filters]")
    print("1 - 1.5x Sharpness")
    print("2 - 1.5x Saturation")
    print("3 - 1.5x Contrast")
    print("4 - Grayscale on")
    print("5 - Embossed on")
    print("6 - Blurred")
    filter_string = input("Modifiers: ")
if not filter_string:
    filter_string = "0"

ImageFilter
os.system("python mosaic.py " + file_path.replace(" ", "§") + " palettes/" + palette + " " + height + " " + filter_string)