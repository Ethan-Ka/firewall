/* The department on the other end of the radio, and its fleet.
 *
 * Everything else on this screen is a reading of what came over the air in the
 * last day. This file is the one thing here that is not: it is who is being
 * listened to, and it changes on the timescale of an apparatus order rather
 * than a poll. So it is a table rather than a fetch.
 *
 * Sources, so a later reader can check rather than trust:
 *
 *   indianafiretrucks.com, Purdue     the fleet. Every unit below is one of
 *   and Purdue/Airport galleries      Chris Allen's photographs, and the specs
 *                                     are the captions he wrote under them --
 *                                     chassis, builder, build number, pump and
 *                                     tank. It is the only source found that
 *                                     has seen all eleven rigs.
 *   purdue.edu/ehps/fire/about        the shift structure behind the crews
 *   purdue.edu/newsroom, 2019-09-27   what Ladder 12 replaced
 *   purdue.edu/newsroom, 2025-01      Medic 16 in service, end of January
 *
 * Nothing here is on the wire, so nothing here can be checked by the program:
 * if a rig is sold and this file is not edited, this file is simply wrong, and
 * quietly. That is the cost of putting reference material on an instrument,
 * and it is paid once here rather than in each panel that reads it.
 *
 * One conflict worth leaving a note on rather than silently picking a side:
 * the gallery captions Battalion 14 as a RAM and the Firefighting Wiki as a
 * Dodge Durango Pursuit. The photograph is of a Durango, so that is what is
 * written below, and this paragraph is here so the next person does not have
 * to find the disagreement again.
 */

/** A photograph of one rig, and who took it.
 *
 *  `src` points at the photographer's own server rather than at a copy in this
 *  repository, which is deliberate: these are all rights reserved and the
 *  watermark is part of the frame. Nothing copyrighted is redistributed by
 *  building this app, the credit travels with the picture, and `href` sends
 *  anybody who wants the full-size version back to the gallery it came from.
 *
 *  See components/Apparatus.tsx for how to override one with your own.
 */
export interface Photo {
  src: string
  by: string
  year: number
  href: string
}

export interface Spec {
  label: string
  value: string
}

export interface Apparatus {
  /** The short form, and the key everything else hangs off. */
  id: string
  /** What dispatch says. */
  name: string
  /** The job, in a word, for under the picture. */
  role: string
  /** Chassis and body, as one line: "2019 Pierce Ascendant 107 ft aerial". */
  rig: string
  /** Which station it sits in. */
  station: 1 | 2
  /** A word for a rig that is not simply in service, or null for one that is.
   *
   *  This was a `frontline` boolean and it was wrong in a way that only showed
   *  up on screen: everything that does not answer a box alarm came out
   *  labelled "reserve", which put the word on the chief's pickup and on three
   *  utility trucks. A spare ambulance is a reserve. An F-250 is not a reserve
   *  anything, it is a truck with a job that is not fighting fire. So the only
   *  thing said here is the thing that is true. */
  standing: 'reserve' | null
  /** Who rides it, when it is staffed as a unit rather than driven by one
   *  person. Null where the answer is "whoever is in the seat". */
  crew: string | null
  specs: Spec[]
  /** The one thing about this rig worth a sentence. Most have none. */
  note: string | null
  photo: Photo
  /** Normalised forms this unit answers to on the air. See `apparatusFor`. */
  aliases: string[]
}

export interface Station {
  n: 1 | 2
  name: string
  where: string
}

export const STATIONS: Station[] = [
  { n: 1, name: 'Clayton W. DeMent Fire Station', where: '1250 W. Third Street' },
  { n: 2, name: 'Station 2', where: 'Purdue University Airport' },
]

/* Where every photograph below came from, said once for the panel's footer.
   Per-picture credit is on the Photo itself; this is the standing one. */
export const PHOTOGRAPHY = {
  by: 'Chris Allen',
  site: 'IndianaFireTrucks.com',
  href: 'https://www.indianafiretrucks.com/Northwestern-Indiana/Tippecanoe-County/Purdue',
} as const

const GALLERY = 'https://www.indianafiretrucks.com/Northwestern-Indiana/Tippecanoe-County/Purdue'
const PHOTOS = 'https://photos.smugmug.com/Northwestern-Indiana/Tippecanoe-County/Purdue'

/* Frontline first, then the rest of what is in the bays, then the airport.
   Not by number, which would open on the chief's pickup. */
export const ROSTER: Apparatus[] = [
  {
    id: 'E11',
    name: 'Engine 11',
    role: 'Engine',
    rig: '2006 American LaFrance Eagle',
    station: 1,
    standing: null,
    crew: 'Captain, engineer, firefighter/paramedic',
    specs: [
      { label: 'Pump', value: '1,500 gpm' },
      { label: 'Tank', value: '750 gal' },
      { label: 'Foam', value: '48 gal' },
      { label: 'Build', value: 'X02338' },
    ],
    note:
      'The first-due engine, and the oldest thing on the floor. American ' +
      'LaFrance stopped building fire apparatus in 2014, so every year this ' +
      'one stays in service is a year of parts found rather than ordered.',
    photo: {
      src: `${PHOTOS}/Purdue/i-wCv4Qf6/0/KHbB7pw7C6wpZdLRc2Gbs3pK92NJ2B3jtPsdB59tQ/XL/e11-run-2019ca-XL.jpg`,
      by: 'Chris Allen', year: 2019, href: `${GALLERY}/Purdue/i-wCv4Qf6`,
    },
    aliases: ['e11', 'engine11'],
  },
  {
    id: 'L12',
    name: 'Ladder 12',
    role: 'Aerial',
    rig: '2019 Pierce Ascendant',
    station: 1,
    standing: null,
    crew: 'Lieutenant, engineer, firefighter',
    specs: [
      { label: 'Aerial', value: '107 ft, rear-mount' },
      { label: 'Pump', value: '1,500 gpm' },
      { label: 'Tank', value: '500 gal' },
      { label: 'Build', value: '#33130' },
    ],
    note:
      'Replaced a 1996 Simon LTI that weighed 71,060 lb and needed 51 ft to ' +
      'turn around. A 107-foot stick on a single rear axle is the whole point ' +
      'of an Ascendant, and it is what gets an aerial down a campus service ' +
      'drive between two residence halls.',
    photo: {
      src: `${PHOTOS}/Purdue/i-wkvcsvw/5/LRWfmczWKjKnKhFNx5wLN5QLCDGJZjSMSFfc2GKdZ/XL/ladder-12-os-2019ca-XL.jpg`,
      by: 'Chris Allen', year: 2019, href: `${GALLERY}/Purdue/i-wkvcsvw`,
    },
    aliases: ['l12', 'ladder12', 't12', 'truck12'],
  },
  {
    id: 'M16',
    name: 'Medic 16',
    role: 'Medic',
    rig: '2024 Ford F-550 / AEV',
    station: 1,
    standing: null,
    crew: 'Two firefighter/paramedics',
    specs: [
      { label: 'Care level', value: 'Advanced life support' },
      { label: 'Type', value: 'I' },
      { label: 'Fleet', value: '0199' },
      { label: 'In service', value: 'January 2025' },
    ],
    note:
      'Old gold and black rather than red, which is the first time the ' +
      'department has painted a rig in the university colours. Carries a ' +
      'powered cot and the restraint positions the nursing and EMT students ' +
      'ride in.',
    photo: {
      src: `${PHOTOS}/Purdue/i-46fctJC/0/KFBqNmmNL79mkdSpbQjS8KgFCZvWPxbFmTN56Zw3v/XL/purdue-m16-os-2026ca-XL.jpg`,
      by: 'Chris Allen', year: 2026, href: `${GALLERY}/Purdue/i-46fctJC`,
    },
    aliases: ['m16', 'medic16', 'a16', 'ambulance16'],
  },
  {
    id: 'M17',
    name: 'Medic 17',
    role: 'Medic',
    rig: '2018 Ford F-450 / Marque Commando',
    station: 1,
    standing: null,
    crew: 'Two firefighter/paramedics',
    specs: [
      { label: 'Care level', value: 'Advanced life support' },
      { label: 'Type', value: 'I' },
      { label: 'Fleet', value: '0738' },
    ],
    note: null,
    photo: {
      src: `${PHOTOS}/Purdue/i-c6F8DNS/6/MBKbMmmxGDfBqN9mJ4kz4sHSQ2ZZ4kSdszrj4GrPB/XL/medic-17-os-2018ca-XL.jpg`,
      by: 'Chris Allen', year: 2018, href: `${GALLERY}/Purdue/i-c6F8DNS`,
    },
    aliases: ['m17', 'medic17', 'a17', 'ambulance17'],
  },
  {
    id: 'M18',
    name: 'Medic 18',
    role: 'Medic',
    rig: '2012 Chevrolet G4500 / Marque',
    station: 1,
    standing: 'reserve',
    crew: null,
    specs: [
      { label: 'Type', value: 'III' },
      { label: 'Build', value: 'MQ4723' },
    ],
    note:
      'The spare. A van-chassis Type III behind two Type I boxes, which is ' +
      'what a reserve ambulance usually is: the one that goes when one of the ' +
      'others is in the shop.',
    photo: {
      src: `${PHOTOS}/Purdue/i-ZkFVBxJ/0/LZDk9R8bPPwvPrLVPRQG8jcx8RJnzLhSqLwCC4Fb7/XL/medic-18-os-2018cma-XL.jpg`,
      by: 'Chris Allen', year: 2018, href: `${GALLERY}/Purdue/i-ZkFVBxJ`,
    },
    aliases: ['m18', 'medic18', 'a18', 'ambulance18'],
  },
  {
    id: 'BC14',
    name: 'Battalion 14',
    role: 'Command',
    rig: '2021 Dodge Durango',
    station: 1,
    standing: null,
    crew: 'Battalion chief',
    specs: [{ label: 'Assigned', value: 'Shift battalion chief' }],
    note:
      'One per shift, and the voice that takes command on the air. If a call ' +
      'in the log below got past "on scene", this is usually who said so.',
    photo: {
      src: `${PHOTOS}/Purdue/i-jzR6vZg/0/MBmkDvLLs2mXh6jkTzsKS39Nff6J77NtMhqS67Zjc/XL/purdue-bc14-os1-2026ca-XL.jpg`,
      by: 'Chris Allen', year: 2026, href: `${GALLERY}/Purdue/i-jzR6vZg`,
    },
    aliases: ['bc14', 'b14', 'battalion14', 'car14', 'chief14'],
  },
  {
    id: 'T10',
    name: 'Truck 10',
    role: 'Command',
    rig: '2025 Ford F-150 4x4',
    station: 1,
    standing: null,
    crew: 'Fire chief',
    specs: [{ label: 'Assigned', value: 'Fire chief' }],
    note:
      'A pickup, despite the number: "truck" here is the vehicle the chief ' +
      'drives, not an aerial. Ladder 12 is the aerial.',
    photo: {
      src: `${PHOTOS}/Purdue/i-Nhzg9rk/0/K8rwWgjPLHCkRNJNjqgHvbxmBzQbc7FwQkFRGpDbK/XL/purdue-chief-ds-2026ca-XL.jpg`,
      by: 'Chris Allen', year: 2026, href: `${GALLERY}/Purdue/i-Nhzg9rk`,
    },
    aliases: ['t10', 'truck10', 'car10', 'chief10'],
  },
  {
    id: 'T19',
    name: 'Truck 19',
    role: 'Utility',
    rig: '2025 Polaris 4x4 UTV',
    station: 1,
    standing: null,
    crew: null,
    specs: [{ label: 'Drive', value: '4x4' }],
    note:
      'The one unit here that can get to a patient in the middle of Slayter ' +
      'Hill or the far side of a home game crowd, which is a real problem on ' +
      'a campus this size and not one an engine solves.',
    photo: {
      src: `${PHOTOS}/Purdue/i-3nGP4nr/0/MjznZhxXFDLThqMtzcd4wVXvGcww5wbRZTQPwzFtg/XL/purdue-tr19-atv-ds-2026ca-XL.jpg`,
      by: 'Chris Allen', year: 2026, href: `${GALLERY}/Purdue/i-3nGP4nr`,
    },
    aliases: ['t19', 'truck19'],
  },
  {
    id: 'T23',
    name: 'Truck 23',
    role: 'Utility',
    rig: '2025 Ford F-250 4x4',
    station: 1,
    standing: null,
    crew: null,
    specs: [{ label: 'Drive', value: '4x4' }],
    note: null,
    photo: {
      src: `${PHOTOS}/Purdue/i-cqr2WBP/0/MgJvtZZLS6f3ZjLRhj89snT7H276P8LQX3BC7qr7j/XL/purdue-tr23-os-2026ca-XL.jpg`,
      by: 'Chris Allen', year: 2026, href: `${GALLERY}/Purdue/i-cqr2WBP`,
    },
    aliases: ['t23', 'truck23'],
  },
  {
    id: 'T24',
    name: 'Truck 24',
    role: 'Utility',
    rig: '2025 Ford F-250 4x4',
    station: 1,
    standing: null,
    crew: null,
    specs: [{ label: 'Drive', value: '4x4' }],
    note: null,
    photo: {
      src: `${PHOTOS}/Purdue/i-3L5L87B/0/LGL2BzRM2QP8s29wBK9w6Dcgz8BL7LbG2gSNXRrXQ/XL/purdue-tr24-os-2026ca-XL.jpg`,
      by: 'Chris Allen', year: 2026, href: `${GALLERY}/Purdue/i-3L5L87B`,
    },
    aliases: ['t24', 'truck24'],
  },
  {
    id: 'E15',
    name: 'Engine 15',
    role: 'Crash tender',
    rig: '2021 Oshkosh Striker 1500',
    station: 2,
    standing: null,
    crew: null,
    specs: [
      { label: 'Pump', value: '1,500 gpm' },
      { label: 'Water', value: '1,500 gal' },
      { label: 'Foam', value: '210 gal' },
      { label: 'Dry chemical', value: '550 lb' },
      { label: 'Build', value: '#809153' },
    ],
    note:
      'The airport rig, and the reason every member of this department sits ' +
      'through a 40-hour aircraft rescue school. Lime yellow rather than red, ' +
      'because on an airfield the thing that has to be seen is not a fire ' +
      'engine among cars but a vehicle among aircraft.',
    photo: {
      src: `${PHOTOS}/Airport/i-GwjDPhm/0/MWqFvp97N92QzxHSrzQMrWLV9gcJw7PQp87vCV6hV/XL/purdue-e15-os-2026ca-XL.jpg`,
      by: 'Chris Allen', year: 2026, href: `${GALLERY}/Airport/i-GwjDPhm`,
    },
    aliases: ['e15', 'engine15'],
  },
]

/* The department itself. Deliberately thin: the panel below is a picture of
   the fleet, and a wall of founding dates and headcounts above it was reading
   as an encyclopedia entry pasted onto an instrument. What survives is what a
   person needs to know to read the rest of the screen -- whose radio this is,
   and what they turn out for. */
export const DEPARTMENT = {
  name: 'Purdue University Fire Department',
  short: 'Purdue FD',
  /* The talkgroup this program is pointed at. It is in .env as
     BCFY_TALKGROUPS=2105:Purdue FD, and it is here so the panel can say out
     loud whose radio the rest of the screen is reading. */
  talkgroup: 2105,
  /* Said in the department's own words, and it is the reason this is a fire
     department and not a campus safety office. */
  claim: 'The only full-time fire department on a Big Ten campus.',
  services: [
    'Structural fire',
    'Advanced life support',
    'Hazardous materials',
    'Aircraft rescue',
    'Confined space',
    'Rope rescue',
    'Elevator rescue',
    'Severe weather',
  ],
} as const

/* ------------------------------------------------------------- resolution */

/** A unit string off the radio, reduced to something comparable.
 *
 *  The wire hands this panel whatever the parser read out of a transcript, and
 *  that is "Engine 11" on one call, "E11" on the next and "engine 11" on a
 *  third. Case, spaces and punctuation carry nothing here, so they go. */
const key = (unit: string) => unit.toLowerCase().replace(/[^a-z0-9]/g, '')

const BY_ALIAS = new Map<string, Apparatus>(
  ROSTER.flatMap((a) => [
    [key(a.id), a] as const,
    [key(a.name), a] as const,
    ...a.aliases.map((x) => [x, a] as const),
  ]),
)

/** The rig a unit string names, or null.
 *
 *  Null is a real answer and is not a bug: mutual aid runs on this talkgroup,
 *  and Lafayette's Engine 3 on a Purdue box is a unit this department does not
 *  own. A panel that guessed would put somebody else's engine on this roster. */
export const apparatusFor = (unit: string): Apparatus | null =>
  BY_ALIAS.get(key(unit)) ?? null

/** Whether a unit string names something in this department at all. */
export const isOurs = (unit: string): boolean => apparatusFor(unit) !== null
