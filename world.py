import array
import struct
import os.path
import collections
import zlib
import itertools

from cStringIO import StringIO

class Region(object):
    def __init__(self, basepath, rx, rz):
        self.rx, self.rz = rx, rz
        self.fp = file(os.path.join(basepath, 'region/r.%s.%s.mcr' % (rx, rz)), 'r+b')
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
        return self.fp.read(size)
    
    def read_chunk(self, cx, cz):
        return Chunk(cx, cz, self.read_chunk_data(cx,cz))
    
    def write_chunk_data(self, cx, cz, data):
        prev_location, prev_size = self.read_chunk_header(cx, cz)
        if len(data) > prev_size:
            raise Exception("Unable to write larger data back into chunk. TODO")
        self.fp.seek(prev_location)
        self.fp.write(data)
    
    def write_chunk(self, chunk):
        self.write_chunk_data(chunk.cx, chunk.cz, chunk.serialize())

class RegionDict(collections.defaultdict):
    def __init__(self, basepath):
        collections.defaultdict.__init__(self)
        self.basepath = basepath
        
    def __missing__(self, (rx, rz)):
        return Region(self.basepath, rx, rz)

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
        
        print "Start", tag.id, name, self.p
        
        payload = self.read_tag(tag)
        
        print "End", name
        
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
            self.blocks = ChunkBlocks(self.root_tag.children['Level'].children['Blocks'].payload)
            self.block_data = ChunkBlockData(self.root_tag.children['Level'].children['Data'].payload)
        
    def serialize(self):
        out = TagOutStream()
        out.write_named_tag(self.root_tag)
        data = zlib.compress(out.getvalue())
        return struct.pack(">LB", len(data), 2) + data

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
        
class ChunkBlockData(object):
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

class World(object):
    def __init__(self, name):
        self.name = name
        self.path = os.path.join(os.path.expanduser("~/Library/Application Support/minecraft/saves/"), name)
        self.regions = RegionDict(self.path)
        
    def read_chunk(self, cx, cz):
        return self.regions[cx // 32, cz // 32].read_chunk(cx, cz)
        
    def write_chunk(self, chunk):
        self.regions[chunk.cx // 32, chunk.cz // 32].write_chunk(chunk)
        
def main():
    world = World('pytestworld')
    chunk = world.read_chunk(0,0)
    
    composition = collections.defaultdict(lambda: 0)
    
    for x,y,z in itertools.product(range(16),range(128),range(16)):
        if chunk.blocks[x,y,z] in (17,18):
            chunk.block_data[x,y,z] = 3
        composition[(chunk.blocks[x,y,z], chunk.block_data[x,y,z])] += 1
    
    world.write_chunk(chunk)
    
    for key, value in sorted(composition.items(), key=lambda (k,v): -v):
        print key, value

    
if __name__ == "__main__":
    main()