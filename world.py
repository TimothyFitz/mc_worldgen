import array
import struct
import os.path
import collections
import zlib
import itertools
import random
import time
import hashlib


from cStringIO import StringIO

sha1 = lambda string: hashlib.sha1(string).hexdigest()

class Region(object):
    def __init__(self, basepath, rx, rz):
        self.rx, self.rz = rx, rz
        self.fp = file(os.path.join(basepath, 'region/r.%s.%s.mcr' % (rx, rz)), 'r+b')
        self.read_header()
        
    def read_header(self):
        self.fp.seek(0)
        self.header_data = self.fp.read(4096)

    def read_chunk_header(self, cx, cz):
        offset = 4 * ((cx % 32) + (cz % 32) * 32)
        chunk_loc_data = self.header_data[offset:offset+4]
        lb1, lb2, lb3, sizeb = struct.unpack(">BBBB", chunk_loc_data)
        
        location = ((lb1 << 16) + (lb2 << 8) + lb3) << 12
        size = sizeb << 12
        return location, size
        
    def read_chunk_data(self, cx, cz):
        location, size = self.read_chunk_header(cx, cz)
        self.fp.seek(location)
        data = self.fp.read(size)
        print "INIT CHUNK", cx, cz, location, size, sha1(data)
        return data
        
    def write_chunk_header(self, cx, cz, location, size):
        offset = 4 * ((cx % 32) + (cz % 32) * 32)
        

        page_location = location >> 12
        size_in_pages = (size >> 12) + 1
        
        assert location % 4096 == 0
        
        lb1 = (page_location >> 16) & 0xFF
        lb2 = (page_location >> 8) & 0xFF
        lb3 = page_location & 0xFF
        
        chunk_header = struct.pack(">BBBB", lb1, lb2, lb3, size_in_pages)
        
        self.fp.seek(offset)
        self.fp.write(chunk_header)
        
        self.read_header()
    
    def read_chunk(self, cx, cz):
        return Chunk(cx, cz, self.read_chunk_data(cx,cz))
    
    def write_chunk_data(self, cx, cz, data):
        location, prev_size = self.read_chunk_header(cx, cz)
        if len(data) > prev_size:
            print "CHUNK", cx, cz, "LARGER THAN PREVIOUS SIZE, REPLACING"
            self.fp.seek(0, 2) # Seek to the end of the file
            location = self.fp.tell()
            print "Seek to end location", location
            self.fp.write("\x00" * (4096 - location % 4096)) # Pad file out
            location = self.fp.tell()
            print "Real location", location
            self.write_chunk_header(cx, cz, location, len(data))
            
        self.fp.seek(location)
        self.fp.write(data)
        self.fp.write("\x00" * (4096 - len(data) % 4096))
    
    def write_chunk(self, chunk):
        cx, cz = chunk.cx, chunk.cz
        data = chunk.serialize()
        self.write_chunk_data(chunk.cx, chunk.cz, data)
        
class TagType(object):
    def __init__(self, id):
        self.id = id

tag_end = TagType(0)
tag_byte = TagType(1)
tag_short = TagType(2)
tag_int = TagType(3)
tag_long = TagType(4)
tag_float = TagType(5)
tag_double = TagType(6)
tag_byte_array  = TagType(7)
tag_string = TagType(8)
tag_list = TagType(9)
tag_compound = TagType(10)

tags = [
    tag_end, tag_byte, tag_short, tag_int, tag_long, tag_float, 
    tag_double, tag_byte_array, tag_string, tag_list, tag_compound
]

tag_sequence_length = {
    tag_string: "h",
    tag_byte_array: "i",
}

simple_tags = {
    tag_byte: "b",
    tag_short: "h",
    tag_int: "i",
    tag_long: "q",
    tag_float: "f",
    tag_double: "d",
}

tag_types = dict([(tag.id, tag) for tag in tags])

class Tag(object):
    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)
        
        if self.type == tag_compound:
            self.children = dict([(tag.name, tag) for tag in self.payload])
                
class TagInStream(object):
    def __init__(self, data):
        self.data = data
        self.p = 0
        
    def read_bytes(self, length):
        result = self.data[self.p:self.p+length]
        self.p += length
        if len(result) < length:
            raise Exception("Read out of stream.")
        return result
    
    def read(self, fmt):
        fmt = ">" + fmt
        return struct.unpack(fmt, self.read_bytes(struct.calcsize(fmt)))[0]
        
    def read_tag(self, tag):
        if tag == tag_end:
            return None
        elif tag in simple_tags:
            return self.read(simple_tags[tag])
        elif tag in tag_sequence_length:
            bytestring = self.read_bytes(self.read(tag_sequence_length[tag]))
            return array.array('B', bytestring)
        elif tag == tag_list:
            list_type = tag_types[self.read("b")]
            list_length = self.read("i")
            return (list_type, [Tag(type=list_type, payload=self.read_tag(list_type)) for n in range(list_length)])
        elif tag == tag_compound:
            payload = []
            while True:
                tag = self.read_named_tag()
                if tag.type == tag_end:
                    break
                else:
                    payload.append(tag)
                    
            return payload
        
        raise Exception("Unknown tag type %r" % tag)
        
    def read_named_tag(self):
        
        tag = tag_types[self.read("B")]

        if tag != tag_end:
            name = self.read_bytes(self.read("H"))
        else:
            name = ""
        
        payload = self.read_tag(tag)
        
        return Tag(type=tag, name=name, payload=payload)

class TagOutStream(object):
    def __init__(self):
        self.out = StringIO()

    def getvalue(self):
        return self.out.getvalue()

    def write(self, fmt, *values):
        self.out.write(struct.pack(">" + fmt, *values))
        
    def write_tag(self, tag):
        if tag.type in simple_tags:
            self.write(simple_tags[tag.type], tag.payload)
        elif tag.type in tag_sequence_length:
            self.write(tag_sequence_length[tag.type], len(tag.payload))
            self.out.write(tag.payload.tostring())
        elif tag.type == tag_list:
            list_type, items = tag.payload
            self.write('b', list_type.id)
            self.write('i', len(items))
            for item in items:
                self.write_tag(item)
        elif tag.type == tag_compound:
            for tag in tag.payload:
                self.write_named_tag(tag)
                
            self.write_named_tag(Tag(type=tag_end))
        
    def write_named_tag(self, tag):
        self.write('B', tag.type.id)
        
        if tag.type != tag_end:
            self.write('H', len(tag.name))
            self.out.write(tag.name)
            
        self.write_tag(tag)

class Chunk(object):
    def __init__(self, cx, cz, raw_data):
        self.cx, self.cz = cx, cz
        if raw_data:
            length, = struct.unpack(">L", raw_data[:4])
            data = zlib.decompress(raw_data[5:5+length])
            self.root_tag = TagInStream(data).read_named_tag()
            level_children = self.root_tag.children['Level'].children
            self.blocks = ChunkBlocks(level_children['Blocks'].payload)
            self.block_data = ChunkNibbleData(level_children['Data'].payload)
            self.skylight = ChunkNibbleData(level_children['SkyLight'].payload)
            self.blocklight = ChunkNibbleData(level_children['BlockLight'].payload)
            self.heightmap = ChunkHeightMap(level_children['HeightMap'].payload)
            
    def relight(self):
        for x,z in itertools.product(range(16), range(16)):
            casting_ray = True
            heightmap = 127
            for y in reversed(range(128)):
                if casting_ray and self.blocks[x,y,z] == 0:
                    sky = 15
                    heightmap = y
                else:
                    sky = 0

                self.skylight[x,y,z] = sky
                self.blocklight[x,y,z] = 0

            self.heightmap[x,z] = heightmap        
        
    def serialize(self):
        out = TagOutStream()
        out.write_named_tag(self.root_tag)
        data = zlib.compress(out.getvalue(), 9)
        internal_header = struct.pack(">LB", len(data), 2)
        return internal_header + data

class ChunkHeightMap(object):
    def __init__(self, heightmap_array):
        self.heightmap_array = heightmap_array

    def __getitem__(self, (x,z)):     
        return self.heightmap_array[x+z*16]

    def __setitem__(self, (x,z), value):
        self.heightmap_array[x+z*16] = value
        
class ChunkBlocks(object):
    def __init__(self, block_array):
        self.block_array = block_array
        
    def __getitem__(self, (x,y,z)):     
        return self.block_array[y + z*128 + x*128*16]
        
    def __setitem__(self, (x,y,z), value):
        self.block_array[y + z*128 + x*128*16] = value

def split_nibble(nibble):
    return [nibble & 0x0F, (nibble & 0xF0) >> 4]
    
def join_nibble((low, high)):
    return (low & 0x0F) + ((high << 4) & 0xF0)
        
class ChunkNibbleData(object):
    def __init__(self, data_array):
        self.data_array = data_array

    def __getitem__(self, (x,y,z)):
        offset = y + z*128 + x*128*16
        return split_nibble(self.data_array[offset >> 1])[offset % 2]

    def __setitem__(self, (x,y,z), value):
        offset = y + z*128 + x*128*16
        data = split_nibble(self.data_array[offset >> 1])
        data[offset % 2] = value
        self.data_array[offset >> 1] = join_nibble(data)     

class RegionDict(collections.defaultdict):
    def __init__(self, basepath):
        collections.defaultdict.__init__(self)
        self.basepath = basepath

    def __missing__(self, (rx, rz)):
        self[rx, rz] = region = Region(self.basepath, rx, rz)
        return region

class ChunkDict(collections.defaultdict):
    def __init__(self, regions):
        collections.defaultdict.__init__(self)
        self.regions = regions

    def __missing__(self, (cx, cz)):
        self[cx, cz] = chunk = self.regions[cx // 32, cz // 32].read_chunk(cx, cz)
        return chunk

def descriptor(key):
    def get_key(self):
        return getattr(self.chunk, key)[self.xyz]
    
    def set_key(self, value):
        getattr(self.chunk, key)[self.xyz] = value
        
    return property(get_key, set_key)

class Voxel(object):
    def __init__(self, world, x,y,z):
        self.chunk = world.chunks[x // 16, z // 16]
        self.xyz = (x % 16, y, z % 16)
        
    def update(self, block=0, data=0):
        self.block = block
        self.data = data
        
    block = descriptor('blocks')
    data = descriptor('block_data')
    skylight = descriptor('skylight')
    blocklight = descriptor('blocklight')    
    
class World(object):
    def __init__(self, name):
        self.name = name
        self.path = os.path.join(os.path.expanduser("~/Library/Application Support/minecraft/saves/"), name)
        self.regions = RegionDict(self.path)
        self.chunks = ChunkDict(self.regions)
        
    def __getitem__(self, (x,y,z)):
        return Voxel(self, x,y,z)
        
    def save(self):
        for chunk in self.chunks.values():
            chunk.relight()
            self.regions[chunk.cx // 32, chunk.cz // 32].write_chunk(chunk)

def carve_cube(world, (x1,y1,z1), (x2,y2,z2), block):
    irange = lambda a,b: range(a, b+1, 1 if b >= a else -1)
    for x,y,z in itertools.product(irange(x1,x2), irange(y1,y2), irange(z1,z2)):
        if x in (x1,x2) or y in (y1,y2) or z in (z1,z2):
            world[x,y,z].block = block
        else:
            world[x,y,z].block = 0
            

N, E, W, S = 1,2,4,8
card_dx = { E: 1, W: -1, N:  0, S: 0 }
card_dy = { E: 0, W:  0, N: -1, S: 1 }
card_opposite = { E: W, W:  E, N:  S, S: N }

def shuffled(iter):
    new_list = list(iter)
    random.shuffle(new_list)
    return new_list

def mazegen(w,h):
    # http://weblog.jamisbuck.org/2010/12/27/maze-generation-recursive-backtracking
    grid = [[0 for x in range(w)] for y in range(h)]
    
    def carve_passages_from(x,y):
        for direction in shuffled([N,E,W,S]):
            nx, ny = x + card_dx[direction], y + card_dy[direction]
            if 0 <= nx < w and 0 <= ny < h and grid[ny][nx] == 0:
                grid[y][x] |= direction
                grid[ny][nx] |= card_opposite[direction]
                carve_passages_from(nx,ny)
                
    carve_passages_from(0,0)
    return grid
    
def print_grid(w,h, cell_space, wall_size):
    grid = mazegen(w,h)
    
    grid[0][0] |= N | W
    grid[h-1][w-1] |= S | E
    
    cell_size = cell_space + wall_size*2
    
    out_w, out_h = w*cell_size, h*cell_size
    
    outgrid = [[True for x in range(out_w)] for y in range(out_h)]
    
    for x,y in itertools.product(range(w), range(h)):
        ox, oy = x*cell_size, y*cell_size
        
        exits = grid[y][x]
        
        # Carve out cell
        for cx, cy in itertools.product(range(cell_space), range(cell_space)):
            outgrid[oy + wall_size + cy][ox + wall_size + cx] = False
        
        # Carve out exits
        if exits & (E | W):
            for x in range(wall_size):
                for y in range(cell_space):
                    if exits & W:
                        outgrid[oy+wall_size+y][ox+x] = False
                    if exits & E:
                        outgrid[oy+wall_size+y][ox+cell_size-wall_size+x] = False
                    
        if exits & (N | S):
            for y in range(wall_size):
                for x in range(cell_space):
                    if exits & N:
                        outgrid[oy+y][ox+wall_size+x] = False
                    if exits & S:
                        outgrid[oy+cell_size-wall_size+y][ox+wall_size+x] = False
    return out_w, out_h, outgrid
                        
    import sys
    out = sys.stdout
    
    for y in range(h*cell_size):
        for x in range(w*cell_size):
            out.write("#" if outgrid[y][x] else " ")
        out.write("\n")

air = 0
sapling = 6
wood = 17
leaves = 18
tall_grass = 31
dead_bush = 32
dandelion = 37
rose = 38
brown_mushroom = 39
red_mushroom = 40
stone_brick = 98
cactus = 81
sugar_cane = 83
vines = 106
    
def main():
    random.seed(101)
    from pprint import pprint
    
    w = h = 8
    gw, gh, grid = print_grid(w=8, h=8, cell_space=6, wall_size=2)
    print gh, gw
    
    world = World('pytestworld')
    
    xz_range = list(itertools.product(range(gw), range(gh)))
    
    for x,z in xz_range:
        for y in range(128):
            voxel = world[x,y,z]
            if voxel.block in (wood, leaves, tall_grass, dandelion, rose, brown_mushroom, red_mushroom, sapling, dead_bush, cactus, sugar_cane, vines):
                voxel.update(air)
    
    max_y = 0
    min_y = 128
    for x,z in xz_range:
        for y in reversed(range(128)):
            if world[x,y,z].block:
                break
        max_y = max(y, max_y)
        min_y = min(y, min_y)
    
    for x,z in xz_range:
        for y in range(1,3): world[x, max_y-y, z].update(stone_brick, random.randrange(3))
        if grid[z][x]:
            for y in range(3): world[x, max_y+y, z].update(stone_brick, random.randrange(3))


    #carve_cube(world, (-16,6,-16), (16,38,16), 15)
    
    world.save()
    
if __name__ == "__main__":
    main()