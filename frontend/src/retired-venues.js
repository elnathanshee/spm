// [SCRUM-100] Retired venues in the Venue Staff workspace. A retired venue leaves
// the catalogue, but its bookings still hold it, so staff need to find them and be
// able to correct the record. This panel only reads; the edit form is the catalogue's.

export function retiredVenues() {
  return {
    venues: [],
    bookingsByVenue: {},
    failedVenueIds: [],
    loading: false,
    error: "",

    async load() {
      this.loading = true;
      this.error = "";
      try {
        const response = await this.apiRequest("/venues?retired=true", { inlineErrors: true });
        if (!response.ok || !Array.isArray(response.data)) {
          this.error = "Retired venues could not be loaded. Try again shortly.";
          return;
        }
        this.venues = response.data;
        await this.loadBookings(response.data);
      } finally {
        this.loading = false;
      }
    },

    async loadBookings(venues) {
      const results = await Promise.all(venues.map(venue => this.apiRequest(`/venues/${venue.id}/bookings`, { inlineErrors: true })));
      const bookingsByVenue = {};
      const failedVenueIds = [];
      venues.forEach((venue, index) => {
        const response = results[index];
        if (response.ok && Array.isArray(response.data)) bookingsByVenue[venue.id] = response.data;
        else failedVenueIds.push(venue.id);
      });
      this.bookingsByVenue = bookingsByVenue;
      this.failedVenueIds = failedVenueIds;
    },

    bookingsFor(venue) { return this.bookingsByVenue[venue.id] || []; },
    bookingsFailed(venue) { return this.failedVenueIds.includes(venue.id); },
  };
}
