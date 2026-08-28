import json, numpy as np, rasterio, os
from rasterio.windows import from_bounds
from rasterio.warp import transform_bounds
from PIL import Image, ImageDraw
os.environ['AWS_NO_SIGN_REQUEST']='YES'
lon0,lat0=73.592,39.476; dlon,dlat=0.020,0.015
bbox=[lon0-dlon,lat0-dlat,lon0+dlon,lat0+dlat]
items={i['id']:i for i in json.load(open('s2_items.json'))}
want=['S2B_43SCD_20260805_0_L2A','S2B_43SCD_20260815_0_L2A','S2C_43SCD_20260820_0_L2A','S2B_43SCD_20260825_0_L2A','S2C_43SCD_20260827_0_L2A','S2C_43SCD_20260817_0_L2A']
def read(href, res):
    with rasterio.open(href) as ds:
        b=transform_bounds('EPSG:4326',ds.crs,*bbox)
        w=from_bounds(*b,ds.transform)
        h=int(round(w.height)); ww=int(round(w.width))
        a=ds.read(1,window=w,out_shape=(int(h*res/ (ds.res[0])) if False else h, ww)).astype(np.float32)
        return a
tiles=[]
for wid in want:
    it=items[wid]; A=it['assets']
    r=read(A['red']['href'],10); g=read(A['green']['href'],10); b=read(A['blue']['href'],10)
    swir=read(A['swir16']['href'],20); nir=read(A['nir']['href'],10)
    swir_up=np.kron(swir,np.ones((2,2)))[:r.shape[0],:r.shape[1]]
    gr=read(A['green']['href'],10)
    ndsi=(gr-swir_up)/(gr+swir_up+1e-6)
    rgb=np.stack([r,g,b],-1)/10000.0
    rgb=np.clip((rgb-0.05)/0.9,0,1)**0.7
    img=(rgb*255).astype(np.uint8)
    # SWIR panel (fresh snow darker in SWIR? actually fresh snow brighter in SWIR than old)
    sw=np.clip(swir_up/10000.0/0.6,0,1); swimg=(np.stack([sw]*3,-1)*255).astype(np.uint8)
    nd=np.clip((ndsi+0.2)/1.2,0,1); ndimg=(np.stack([nd]*3,-1)*255).astype(np.uint8)
    im=Image.fromarray(np.concatenate([img,swimg,ndimg],1)).resize((3*r.shape[1]*2, r.shape[0]*2),Image.NEAREST)
    d=ImageDraw.Draw(im); d.text((5,5),wid[9:17]+' RGB | SWIR B11 | NDSI  cloud %.0f%%'%it['properties']['eo:cloud_cover'],fill=(255,0,0))
    # mark Camp2
    x=int((lon0-bbox[0])/(bbox[2]-bbox[0])*r.shape[1]*2); y=int((bbox[3]-lat0)/(bbox[3]-bbox[1])*r.shape[0]*2)
    for k in range(3):
        d.ellipse((x-6+k*r.shape[1]*2,y-6,x+6+k*r.shape[1]*2,y+6),outline=(255,0,0),width=2)
    tiles.append(im)
    print(wid, r.shape, 'mean SWIR snow-area %.0f'%swir_up[ndsi>0.4].mean(), 'NDSI>0.4 frac %.2f'%(ndsi>0.4).mean(), 'mean red %.0f'%r.mean())
W=max(t.width for t in tiles); H=sum(t.height for t in tiles)
M=Image.new('RGB',(W,H)); y=0
for t in tiles: M.paste(t,(0,y)); y+=t.height
M.save('s2_montage.png'); print(M.size)
