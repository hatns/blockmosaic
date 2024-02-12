import sys
import os, os.path
from PIL import Image, ImageOps, ImageFilter, ImageEnhance
from multiprocessing import Process, Queue, cpu_count
from litemapy import Region, BlockState
Image.MAX_IMAGE_PIXELS = None



# Change these 3 config parameters to suit your needs...
TILE_SIZE      = 16		# height/width of mosaic tiles in pixels
TILE_MATCH_RES = 1		# tile matching resolution (higher values give better fit but require more processing)

TILE_BLOCK_SIZE = TILE_SIZE / max(min(TILE_MATCH_RES, TILE_SIZE), 1)
WORKER_COUNT = max(cpu_count(), 1)
EOQ_VALUE = None

class TileProcessor:
	def __init__(self, tiles_directory):
		self.tiles_directory = tiles_directory

	def __process_tile(self, tile_path):
		try:
			img = Image.open(tile_path)
			img = ImageOps.exif_transpose(img)

			# tiles must be square, so get the largest square that fits inside the image
			w = img.size[0]
			h = img.size[1]
			min_dimension = min(w, h)
			w_crop = (w - min_dimension) / 2
			h_crop = (h - min_dimension) / 2
			img = img.crop((w_crop, h_crop, w - w_crop, h - h_crop))

			large_tile_img = img.resize((TILE_SIZE, TILE_SIZE), Image.BICUBIC)
			small_tile_img = img.resize((int(TILE_SIZE/TILE_BLOCK_SIZE), int(TILE_SIZE/TILE_BLOCK_SIZE)), Image.BICUBIC)

			return (large_tile_img.convert('RGB'), small_tile_img.convert('RGB'))
		except:
			return (None, None)

	def get_tiles(self):
		large_tiles = []
		small_tiles = []
		names = []

		print('Reading tiles from {}...'.format(self.tiles_directory))

		# search the tiles directory recursively
		for root, subFolders, files in os.walk(self.tiles_directory):
			for tile_name in files:
				print('Reading {:40.40}'.format(tile_name), flush=True, end='\r')
				tile_path = os.path.join(root, tile_name)
				large_tile, small_tile = self.__process_tile(tile_path)
				if large_tile:
					names.append(tile_name)
					large_tiles.append(large_tile)
					small_tiles.append(small_tile)


		print('Processed {} tiles.'.format(len(large_tiles)))

		return (large_tiles, small_tiles, names)

class TargetImage:
	def __init__(self, image_path, height, filter_string):
		self.image_path = image_path
		self.HEIGHT = height
		self.filter_string = filter_string

	def get_data(self):
		print('Processing main image...')
		img = Image.open(self.image_path)
		
		# match filter string to filters
		s = self.filter_string
		if "1" in s:
			convertor = ImageEnhance.Sharpness(img)
			img = convertor.enhance(1.5)
		if "2" in s:
			convertor = ImageEnhance.Color(img)
			img = convertor.enhance(1.5)
		if "3" in s:
			convertor = ImageEnhance.Contrast(img)
			img = convertor.enhance(1.5)
		if "4" in s:
			img = ImageOps.grayscale(img)
		if "5" in s:
			img = img.filter(ImageFilter.EMBOSS)
		if "6" in s:
			img = img.filter(ImageFilter.GaussianBlur(5))
			
			

		w = int(img.width * (self.HEIGHT / img.height))
		h = self.HEIGHT
		large_img = img.resize((w, h), Image.BICUBIC)
		w_diff = (w % TILE_SIZE)/2
		h_diff = (h % TILE_SIZE)/2
		
		# if necessary, crop the image slightly so we use a whole number of tiles horizontally and vertically
		if w_diff or h_diff:
			large_img = large_img.crop((w_diff, h_diff, w - w_diff, h - h_diff))

		small_img = large_img.resize((int(w/TILE_BLOCK_SIZE), int(h/TILE_BLOCK_SIZE)), Image.BICUBIC)

		image_data = (large_img.convert('RGB'), small_img.convert('RGB'))

		print('Main image processed.')

		return image_data

class TileFitter:
	def __init__(self, tiles_data):
		self.tiles_data = tiles_data

	def __get_tile_diff(self, t1, t2, bail_out_value):
		diff = 0
		for i in range(len(t1)):
			#diff += (abs(t1[i][0] - t2[i][0]) + abs(t1[i][1] - t2[i][1]) + abs(t1[i][2] - t2[i][2]))
			diff += ((t1[i][0] - t2[i][0])**2 + (t1[i][1] - t2[i][1])**2 + (t1[i][2] - t2[i][2])**2)
			if diff > bail_out_value:
				# we know already that this isn't going to be the best fit, so no point continuing with this tile
				return diff
		return diff

	def get_best_fit_tile(self, img_data):
		best_fit_tile_index = None
		min_diff = sys.maxsize
		tile_index = 0

		# go through each tile in turn looking for the best match for the part of the image represented by 'img_data'
		for tile_data in self.tiles_data:
			diff = self.__get_tile_diff(img_data, tile_data, min_diff)
			if diff < min_diff:
				min_diff = diff
				best_fit_tile_index = tile_index
			tile_index += 1

		return best_fit_tile_index

def fit_tiles(work_queue, result_queue, tiles_data):
	# this function gets run by the worker processes, one on each CPU core
	tile_fitter = TileFitter(tiles_data)

	while True:
		try:
			img_data, img_coords = work_queue.get(True)
			if img_data == EOQ_VALUE:
				break
			tile_index = tile_fitter.get_best_fit_tile(img_data)
			result_queue.put((img_coords, tile_index))
		except KeyboardInterrupt:
			pass

	# let the result handler know that this worker has finished everything
	result_queue.put((EOQ_VALUE, EOQ_VALUE))

class ProgressCounter:
	def __init__(self, total):
		self.total = total
		self.counter = 0

	def update(self):
		self.counter += 1
		print("Progress: {:04.1f}%".format(100 * self.counter / self.total), flush=True, end='\r')

class MosaicImage:
	def __init__(self, original_img, img_path: str):
		
		self.image = Image.new(original_img.mode, original_img.size)
		self.x_tile_count = int(original_img.size[0] / TILE_SIZE)
		self.y_tile_count = int(original_img.size[1] / TILE_SIZE)
		self.img_path = img_path
		self.total_tiles  = self.x_tile_count * self.y_tile_count
		self.tile_list = []
		self.reg = Region(0, 0, 0, self.x_tile_count, self.y_tile_count, 1)

	def add_tile(self, tile_data, coords, tile_name: str):
		coordinate = (int(coords[0] / 16), int(coords[1] / 16)) 
		self.tile_list.append((coordinate, tile_name))
		img = Image.new('RGB', (TILE_SIZE, TILE_SIZE))
		img.putdata(tile_data)
		self.image.paste(img, coords)

	def build_schematic(self, original_img_name):
		schematic_name = original_img_name.split(".")[0] + ".litematic"
		schematic = self.reg.as_schematic(name=schematic_name.split(".")[0], author="Minepyxel", description="Made with Minepyxel, a Minecraft fork of PhotoMosaic")
		for tile in self.tile_list:
			tile_name = tile[1].removesuffix(".png") # convert to just the block name
			self.reg.setblock(tile[0][0], self.y_tile_count - 1 - tile[0][1] - 1 , 0, BlockState(tile_name))
		schematic.save(os.getenv("appdata") + "/.minecraft/schematics/"+ schematic_name)

	def save(self):
		self.img_path = self.img_path.replace("\\", "/")
		self.img_path = self.img_path.replace("\\\\", "/")
		path = self.img_path.split("/")[-1]
		self.build_schematic(path)
		self.image.save("imgs/"+path.replace(".png", "_mosaic.png"))


def build_mosaic(result_queue, all_tile_data_large, original_img_large, names, img_path):
	mosaic = MosaicImage(original_img_large, img_path)

	active_workers = WORKER_COUNT
	while True:
		try:
			img_coords, best_fit_tile_index = result_queue.get()

			if img_coords == EOQ_VALUE:
				active_workers -= 1
				if not active_workers:
					break
			else:
				tile_data = all_tile_data_large[best_fit_tile_index]
				tile_name = names[best_fit_tile_index]
				mosaic.add_tile(tile_data, img_coords, tile_name)

		except KeyboardInterrupt:
			pass
	
	mosaic.save()
	img_path = img_path.split("/")[-1].split(".")
	img_path.insert(1, ".")
	img_path = "".join(img_path)
	print('\nFinished, output is', os.curdir.replace("\\", "/") + "/imgs/" + img_path)

def compose(original_img, tiles, img_path):
	print('Building mosaic, press Ctrl-C to abort...')
	original_img_large, original_img_small = original_img
	tiles_large, tiles_small, names = tiles

	mosaic = MosaicImage(original_img_large, img_path)

	all_tile_data_large = [list(tile.getdata()) for tile in tiles_large]
	all_tile_data_small = [list(tile.getdata()) for tile in tiles_small]

	work_queue   = Queue(WORKER_COUNT)	
	result_queue = Queue()

	try:
		# start the worker processes that will build the mosaic image
		Process(target=build_mosaic, args=(result_queue, all_tile_data_large, original_img_large, names, img_path)).start()

		# start the worker processes that will perform the tile fitting
		for n in range(WORKER_COUNT):
			Process(target=fit_tiles, args=(work_queue, result_queue, all_tile_data_small)).start()

		progress = ProgressCounter(mosaic.x_tile_count * mosaic.y_tile_count)
		for x in range(mosaic.x_tile_count):
			for y in range(mosaic.y_tile_count):
				large_box = (x * TILE_SIZE, y * TILE_SIZE, (x + 1) * TILE_SIZE, (y + 1) * TILE_SIZE)
				small_box = (x * TILE_SIZE/TILE_BLOCK_SIZE, y * TILE_SIZE/TILE_BLOCK_SIZE, (x + 1) * TILE_SIZE/TILE_BLOCK_SIZE, (y + 1) * TILE_SIZE/TILE_BLOCK_SIZE)
				work_queue.put((list(original_img_small.crop(small_box).getdata()), large_box))
				progress.update()

	except KeyboardInterrupt:
		print('\nHalting, saving partial image please wait...')

	finally:
		# put these special values onto the queue to let the workers know they can terminate
		for n in range(WORKER_COUNT):
			work_queue.put((EOQ_VALUE, EOQ_VALUE))

def show_error(msg):
	print('ERROR: {}'.format(msg))

def mosaic(img_path, tiles_path, height, filter_string):
	image_data = TargetImage(img_path, height, filter_string).get_data()
	tiles_data = TileProcessor(tiles_path).get_tiles()
	if tiles_data[0]:
		compose(image_data, tiles_data, img_path)
	else:
		show_error("No images found in tiles directory '{}'".format(tiles_path))

if __name__ == '__main__':
	if len(sys.argv) < 3:
		show_error('Usage: {} <image> <tiles directory> <height>\r'.format(sys.argv[0]))
	else:
		source_image = sys.argv[1].replace("§", " ")
		tile_dir = sys.argv[2]
		height = int(sys.argv[3]) * 16
		filter_string = sys.argv[4]
		
		if not os.path.isfile(source_image):
			show_error("Unable to find image file '{}'".format(source_image))
		elif not os.path.isdir(tile_dir):
			show_error("Unable to find tile directory '{}'".format(tile_dir))
		else:
			mosaic(source_image, tile_dir, height, filter_string)