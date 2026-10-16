/** Destination photos on the landing page and in the footer. All from Pexels (free to use, no attribution required
 *  by the Pexels license), downloaded at 1600 px wide (sharp on high-density screens; Next.js serves smaller copies
 *  where they're shown small) and kept in /public/photos. The Pexels id is kept so each can be traced. */
export type Photo = { city: string; country: string; src: string; alt: string; pexels: number; focus?: string };  // focus: CSS object-position

export const DESTINATION_PHOTOS: Photo[] = [
  { city: "Agra", country: "Uttar Pradesh", src: "/photos/agra.jpg", alt: "The Taj Mahal and its long reflecting pool under a blue sky", pexels: 3881104 },
  { city: "Dubai", country: "UAE", src: "/photos/dubai.jpg", alt: "Dubai's skyline with the Burj Khalifa", pexels: 1537493, focus: "50% 30%" },
  { city: "Jaipur", country: "Rajasthan", src: "/photos/jaipur.jpg", alt: "The pink sandstone face of Hawa Mahal in Jaipur", pexels: 15395061 },
  { city: "Bali", country: "Indonesia", src: "/photos/bali.jpg", alt: "Green rice terraces in Bali", pexels: 35428411 },
  { city: "Varanasi", country: "Uttar Pradesh", src: "/photos/varanasi.jpg", alt: "Boats and old buildings on the ghats of the Ganges in Varanasi", pexels: 12112985 },
  { city: "Leh", country: "Ladakh", src: "/photos/leh.jpg", alt: "Leh town below bare mountains and a wide blue sky", pexels: 34484892 },
  { city: "Udaipur", country: "Rajasthan", src: "/photos/udaipur.jpg", alt: "Udaipur's City Palace beside Lake Pichola", pexels: 18526645 },
  { city: "Alleppey", country: "Kerala", src: "/photos/alleppey.jpg", alt: "A houseboat on the palm-lined backwaters of Kerala", pexels: 35347829, focus: "25% 50%" },
];

export const TRAIN_PHOTO = { city: "Indian Railways", country: "", src: "/photos/train.jpg", alt: "An Indian Railways train curving along the tracks", pexels: 29528961 };

/** Wide landscapes that take turns behind the footer's wordmark. */
export const FOOTER_PHOTOS = ["Udaipur", "Leh", "Bali"].map((c) => DESTINATION_PHOTOS.find((d) => d.city === c)!);
