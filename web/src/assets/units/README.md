# Photographs of the rigs

The roster panel shows Chris Allen's photographs, loaded from his own server at
IndianaFireTrucks.com and credited on every tile. They are all rights reserved,
so this repository holds no copies of them -- see the header of
`src/components/Apparatus.tsx` for why it is arranged that way.

That means the tiles need a route to the internet. If this display lives
somewhere that does not have one, or you took your own photographs, or you have
permission for somebody else's, drop files in here named for the unit. A local
file wins over the remote one, at build time, with no code change:

```
e11.jpg    l12.jpg    m16.jpg    m17.jpg    m18.jpg    bc14.jpg
t10.jpg    t19.jpg    t23.jpg    t24.jpg    e15.jpg
```

`.jpg`, `.jpeg`, `.png`, `.webp` and `.avif` are all picked up. Tiles are 3:2
and `object-cover`, so a landscape frame of the rig side-on is what fits.

Nothing in here is committed by accident -- these are your photographs, and
whether they belong in the repository is a licensing question only you can
answer for the picture you have.
